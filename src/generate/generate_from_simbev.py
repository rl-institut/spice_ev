#!/usr/bin/env python3

import csv
import datetime
import json
from pathlib import Path
import random
import warnings

from src.battery import Battery
from src.loading_curve import LoadingCurve


def parse_vehicle_types(tech_data):
    """Get vehicle data from SimBEV metadata

    :param tech_data: dictionary which contains the tech data part of a SimBEV metadata json
    :type tech_data: dict
    :returns: vehicle types
    :rtype: dict
    """
    predefined_vehicle_types = {}
    for name, data in tech_data.items():
        max_charge = max(data["max_charging_capacity_slow"], data["max_charging_capacity_fast"])
        predefined_vehicle_types[name] = {
            "name": name,
            "capacity": data["battery_capacity"],
            "mileage": round(data["energy_consumption"] * 100, 2),
            "charging_curve": [[0, max_charge], [1, max_charge]],
            "min_charging_power": 0.1,
        }
    return predefined_vehicle_types


def generate_from_simbev(args):
    """Generate a scenario JSON from SimBEV results.

    :param args: input arguments
    :type args: argparse.Namespace
    :return: scenario
    :rtype: dict
    """

    # read SimBEV metadata
    simbev_path = Path(args.simbev)
    assert simbev_path.exists(), f"SimBEV directory {args.simbev} does not exist."
    metadata_path = Path(simbev_path, "metadata_simbev_run.json")
    assert metadata_path.exists(), "Metadata file does not exist in SimBEV directory."
    with open(metadata_path) as f:
        metadata = json.load(f)

    # get pathlist of vehicle CSV files
    if not args.region:
        pathlist = list(simbev_path.rglob('*_events.csv'))
    else:
        region_path = Path(simbev_path, args.region)
        pathlist = list(region_path.rglob('*_events.csv'))
    pathlist.sort()

    def datetime_from_timestep(timestep):
        assert type(timestep) == int
        return start + (interval * timestep)

    # take start time from SimBEV metadata
    start = datetime.datetime.strptime(metadata["config"]["basic"]["start_date"], "%Y-%m-%d")
    # define interval for simulation
    interval = datetime.timedelta(minutes=args.interval)
    n_intervals = 0

    # get defined vehicle types
    if args.vehicle_types is None:
        print("No definition of vehicle types found, using vehicles from metadata.")
        predefined_vehicle_types = parse_vehicle_types(metadata["tech_data"])
    else:
        ext = args.vehicle_types.split('.')[-1]
        if ext != "json":
            warnings.warn("File extension mismatch: vehicle type file should be '.json'.")
        with open(args.vehicle_types) as f:
            predefined_vehicle_types = json.load(f)

    # INITIALIZE CONSTANTS AND EVENTS
    vehicle_types = {}
    vehicles = {}
    charging_stations = {}
    events = {
        "grid_operator_signals": [],
        "external_load": {},
        "energy_feed_in": {},
        "vehicle_events": []
    }

    # count number of trips for which desired_soc is above min_soc
    trips_above_min_soc = 0
    trips_total = 0

    # set vehicle type if present in vehicle_types.json
    for v_type, count in metadata['car_sum'].items():
        if count > 0:
            vehicle_types.update({v_type: predefined_vehicle_types[v_type]})

    # update info of external CSV files
    ext_info = {
        "external_load": "include_ext_load_csv",
        "energy_feed_in": "include_feed_in_csv",
        "energy_price_from_csv": "include_price_csv",
    }
    for info, field in ext_info.items():
        option = field + "_option"
        if vars(args)[field] and vars(args)[option]["start_time"] is None:
            vars(args)[option]["start_time"] = start.isoformat()
        if vars(args)[option]:
            if info == "energy_price_from_csv":
                events[info] = vars(args)[option]
            else:
                events[info][vars(args)[field]] = vars(args)[option]

    if args.include_price_csv is None:
        if args.seed is not None and args.seed < 0:
            # use single, fixed price
            events["grid_operator_signals"].append({
                "signal_time": start.isoformat(),
                "grid_connector_id": "GC1",
                "start_time": start.isoformat(),
                "cost": {
                    "type": "fixed",
                    "value": -args.seed
                }
            })
        else:
            # random price
            # set seed from input (repeatability)
            random.seed(args.seed)
            # price remains stable for X hours
            price_stable_hours = 6
            # every X timesteps, generate new price signal
            price_interval = datetime.timedelta(hours=price_stable_hours) / interval

    # GENERATE VEHICLE EVENTS: iterate over input files
    for csv_path in pathlist:
        # get vehicle id from file name
        v_id = str(csv_path.stem)[:-7]

        with open(csv_path, newline='') as csvfile:
            reader = csv.DictReader(csvfile)

            # set vehicle info from first data row
            # update in vehicles, regardless if known before or not
            v_info = v_id.split("_")
            # get vehicle type from the first two parts of the csv name
            v_type = '_'.join(v_info[:2])

            # vehicle type must be known
            assert v_type in vehicle_types, f"Unknown type for {v_id}: {v_type}."
            if v_id in vehicles:
                num_similar_name = sum([1 for v in vehicles.keys() if v.startswith(v_id)])
                v_id_new = "{}_{}".format(v_id, num_similar_name + 1)
                if args.verbose > 0:
                    warnings.warn(f"Vehicle name '{v_id}' is not unique! "
                                  f"Renamed to '{v_id_new}'.")
                v_id = v_id_new

            # check that capacities match
            vehicle_capacity = vehicle_types[v_type]["capacity"]
            # take capacity from vehicle file name
            file_capacity = int(v_info[-1][:-3])
            if vehicle_capacity != file_capacity and args.verbose > 0:
                warnings.warn(f"Capacities of vehicle type '{v_type}' don't match!"
                              f"In file name: '{file_capacity}', in script: '{vehicle_capacity}'. "
                              "Using value from file.")
                vehicle_capacity = file_capacity
                vehicle_types[v_type]["capacity"] = file_capacity

            # set initial charge
            last_arrival_idx = None
            soc_needed = 0.0
            arrival = None
            departure = datetime_from_timestep(0)

            # iterate next timesteps
            for idx, row in enumerate(reader):
                if idx == 0:
                    # save initial vehicle data
                    vehicle_soc = float(row["soc_start"])
                    vehicles[v_id] = {
                        "connected_charging_station": None,
                        "soc": vehicle_soc,
                        "vehicle_type": v_type
                    }
                    battery = Battery(
                        capacity=vehicle_capacity,
                        loading_curve=LoadingCurve(vehicle_types[v_type]["charging_curve"]),
                        soc=vehicle_soc,
                        efficiency=vehicle_types[v_type].get("efficiency", 0.95)
                    )
                is_charge_event = False
                # read info from row
                location = row["location"]
                cs_power = float(row["station_charging_capacity"])
                consumption = abs(min(float(row["energy"]), 0))

                # general sanity checks
                simbev_soc_start = float(row["soc_start"])
                simbev_soc_end = float(row["soc_end"])
                # SoC must not be negative
                assert simbev_soc_start >= 0 and simbev_soc_end >= 0, \
                    f"SimBEV created negative SoC for {v_id} in row {idx + 1}."
                # might want to avoid very low battery levels (configurable in config)
                soc_threshold = args.min_soc_threshold
                if args.verbose > 0 and (
                        simbev_soc_start < soc_threshold
                        or simbev_soc_end < soc_threshold):
                    warnings.warn(f"SimBEV created very low SoC for {v_id} "
                                  f"in row {idx + 1}.")

                simbev_demand = max(float(row["energy"]), 0)
                assert cs_power > 0 or simbev_demand == 0, \
                    f"Charging event without charging station: {v_id} in row {idx + 1}."

                cs_present = cs_power > 0
                assert (not cs_present) or consumption == 0, \
                    f"Consumption while charging for {v_id} in row {idx + 1}."

                # get maximum length of timesteps
                departure_idx = int(row["event_start"]) + int(row["event_time"])
                n_intervals = max(n_intervals, departure_idx)

                # actual driving and charging behavior
                if not args.ignore_simbev_soc:
                    if cs_present and float(row["energy"]) > 0:
                        # arrival at new CS: use info from SimBEV directly
                        is_charge_event = True
                        desired_soc = float(row["soc_end"])
                        delta_soc = (vehicle_soc - float(row["soc_start"]))

                        # check if feasible: simulate with battery
                        # set battery SoC to level when arriving
                        battery.soc = float(row["soc_start"])
                        charge_duration = int(row["event_time"]) * interval
                        battery.load(charge_duration, cs_power)
                        if battery.soc < float(row["soc_end"]) and args.verbose > 0:
                            warnings.warn(f"Can't fulfill charging request for {v_id} in "
                                          f"ts {row['timestamp']}. Desired SoC is set to "
                                          f"{desired_soc:.3f}, possible: {battery.soc:.3f}.")
                        vehicle_soc = desired_soc
                else:
                    # tolerance for sanity checks, required due to possible rounding
                    # differences between SimBEV and SpiceEV
                    tolerance = 1e-5
                    # compute needed power and desired SoC independent of SimBEV
                    if not cs_present:
                        # no charging station or don't need to charge
                        # just increase charging demand based on consumption
                        soc_needed += consumption / vehicle_capacity
                        assert soc_needed <= 1 + vehicle_soc + tolerance, (
                            f"Consumption too high for {v_id} in row {idx + 1}: "
                            f"vehicle charged to {vehicle_soc}, needs SoC of {soc_needed} "
                            f"({soc_needed * vehicle_capacity} kWh). This might be caused by "
                            f"rounding differences between SimBEV and SpiceEV.")
                    else:
                        # charging station present
                        is_charge_event = True

                        if last_arrival_idx is None:
                            # first charge: initial must be enough
                            assert vehicle_soc >= soc_needed - tolerance, (
                                f"Initial charge for {v_id} is not sufficient. This might "
                                f"be caused by rounding differences between SimBEV and SpiceEV.")
                        else:
                            # update desired SoC from last charging event
                            # this much charge must be in battery when leaving CS
                            # to reach next CS (the one from current row)
                            desired_soc = max(args.min_soc, soc_needed)

                            trips_above_min_soc += desired_soc > args.min_soc
                            trips_total += 1

                            # this much must be charged
                            delta_soc = max(desired_soc - vehicle_soc, 0)

                            # check if charging is possible in ideal case
                            last_arrival_event = events["vehicle_events"][last_arrival_idx]
                            cs_id = last_arrival_event["update"]["connected_charging_station"]
                            charge_duration = departure - arrival
                            possible_energy = (charging_stations[cs_id]["max_power"] *
                                               charge_duration.total_seconds() / 3600)
                            possible_soc = possible_energy / vehicle_capacity

                            if delta_soc > possible_soc:
                                warnings.warn(
                                    f"Can't fulfill charging request for '{v_id}' in ts "
                                    f"{((arrival - start) / interval):.0f}. Need "
                                    f"{(desired_soc * vehicle_capacity):.2f} kWh in "
                                    f"{(charge_duration.total_seconds() / 3600):.2f} h "
                                    f"({(charge_duration / interval):.0f} ts). "
                                    f"Possible within standing time: {possible_energy} kWh.")

                            # update last charge event info: set desired SOC
                            if last_arrival_idx is not None:
                                events["vehicle_events"][last_arrival_idx]["update"]["desired_soc"]\
                                    = desired_soc

                            # simulate charging
                            vehicle_soc = max(vehicle_soc, desired_soc)

                        # reset desired SoC for next trip
                        desired_soc = 0

                        # update vehicle SOC: with how much SOC does vehicle arrive at new CS?
                        vehicle_soc -= soc_needed

                if is_charge_event:
                    # initialize new charge event

                    # setup charging point at location
                    cs_id = f"{v_id}_{location}"
                    if (cs_id in charging_stations
                            and charging_stations[cs_id]["max_power"] != cs_power):
                        # same location type, different cs_power: build new CS
                        cs_id = "{}_{}".format(cs_id, idx)
                    if cs_id not in charging_stations:
                        charging_stations[cs_id] = {
                            "max_power": cs_power,
                            "min_power": (args.cs_power_min if args.cs_power_min is not None
                                          else 0.1 * cs_power),
                            "parent": "GC1"
                        }

                    # generate vehicle events
                    # arrival at new CS
                    arrival_idx = int(row["event_start"])
                    arrival = datetime_from_timestep(arrival_idx)
                    assert arrival >= departure, (
                        f"Order of vehicle {v_id} wrong in timestep {arrival_idx}, "
                        f"has been standing already.")
                    departure = datetime_from_timestep(departure_idx)
                    delta_soc = soc_needed if args.ignore_simbev_soc else delta_soc
                    events["vehicle_events"].append({
                        "signal_time": arrival.isoformat(),
                        "start_time": arrival.isoformat(),
                        "vehicle_id": v_id,
                        "event_type": "arrival",
                        "update": {
                            "connected_charging_station": cs_id,
                            "estimated_time_of_departure": departure.isoformat(),
                            "desired_soc": desired_soc,  # may be None, updated later
                            "soc_delta": -delta_soc
                        }
                    })
                    # update last departure
                    if last_arrival_idx is not None:
                        events["vehicle_events"][last_arrival_idx+1]["update"][
                            "estimated_time_of_arrival"] = arrival.isoformat()
                    last_arrival_idx = len(events["vehicle_events"]) - 1

                    # departure from CS
                    events["vehicle_events"].append({
                        "signal_time": departure.isoformat(),
                        "start_time": departure.isoformat(),
                        "vehicle_id": v_id,
                        "event_type": "departure",
                        "update": {
                            "estimated_time_of_arrival": None  # updated at next arrival
                        }
                    })

                    # reset distance (needed charge) to next CS
                    soc_needed = 0.0

    # random price: each price interval, generate new price
    while (
        not args.include_price_csv
        and (args.seed is None or args.seed >= 0)
        and n_intervals >= price_interval * len(events["grid_operator_signals"])
    ):
        # at which timestep is price updated?
        price_update_idx = int(
            len(events["grid_operator_signals"]) * price_interval)
        start_time = datetime_from_timestep(price_update_idx)
        # price signal known one day ahead
        signal_time = max(start, start_time - datetime.timedelta(days=1))
        if 6 < start_time.hour < 18:
            # daytime: ~15ct
            events['grid_operator_signals'].append({
                "signal_time": signal_time.isoformat(),
                "grid_connector_id": "GC1",
                "start_time": start_time.isoformat(),
                "cost": {
                    "type": "fixed",
                    "value": 0.15 + random.gauss(0, 0.05)
                }
            })
        else:
            # nighttime: ~5ct
            events['grid_operator_signals'].append({
                "signal_time": signal_time.isoformat(),
                "grid_connector_id": "GC1",
                "start_time": start_time.isoformat(),
                "cost": {
                    "type": "fixed",
                    "value": 0.15 + random.gauss(0, 0.05)
                }
            })

    assert len(vehicles) > 0, f"No vehicles found in {args.simbev}."

    # number of trips for which desired_soc is above min_soc
    if trips_above_min_soc and args.verbose > 0:
        print(f"{trips_above_min_soc} of {trips_total} trips "
              f"use more than {args.min_soc * 100}% capacity")

    # create final dict
    return {
        "scenario": {
            "start_time": start.isoformat(),
            "interval": args.interval,
            "n_intervals": n_intervals,
            "discharge_limit": args.discharge_limit,
        },
        "constants": {
            "vehicle_types": vehicle_types,
            "vehicles": vehicles,
            "grid_connectors": args.gc,
            "charging_stations": charging_stations,
            "batteries": args.battery,
            "photovoltaics": args.pv,
        },
        "events": events,
    }
