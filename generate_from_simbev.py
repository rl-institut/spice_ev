#!/usr/bin/env python3

import argparse
import csv
import datetime
import json
import warnings
from pathlib import Path
import random

from src.util import set_options_from_config
from src.battery import Battery
from src.loading_curve import LoadingCurve


def parse_vehicle_types(tech_data):
    """Get vehicle data from SimBEV metadata

    :param tech_data: dictionary which containts the tech data part of a SimBEV metadata json
    :type tech_data: dict
    :returns: vehicle types
    :rtype: dict
    """
    vehicle_types = {}
    for name, data in tech_data.items():
        max_charge = max(data["max_charging_capacity_slow"], data["max_charging_capacity_fast"])
        vehicle_types[name] = {
            "name": name,
            "capacity": data["battery_capacity"],
            "mileage": round(data["energy_consumption"] * 100, 2),
            "charging_curve": [[0, max_charge], [1, max_charge]],
            "min_charging_power": 0.1,
        }
    return vehicle_types


def generate_from_simbev(args):
    """Generate a scenario JSON from SimBEV results.

    :param args: input arguments
    :type args: argparse.Namespace
    :raises SystemExit: if required arguments (*output* and *simbev*) are missing
    """
    missing = [arg for arg in ["output", "simbev"] if vars(args).get(arg) is None]
    if missing:
        raise SystemExit("The following arguments are required: {}".format(", ".join(missing)))

    # read simbev metadata
    simbev_path = Path(args.simbev)
    assert simbev_path.exists(), f"SimBEV directory {args.simbev} does not exist."
    metadata_path = Path(simbev_path, "metadata_simbev_run.json")
    assert metadata_path.exists(), "Metadata file does not exist in SimBEV directory."
    with open(metadata_path) as f:
        metadata = json.load(f)

    # first monday of 2021
    # SimBEV uses MiD data and creates data for an exemplary week, so there are no exact dates.
    start = datetime.datetime.strptime(metadata["config"]["basic"]["start_date"], "%Y-%m-%d")
    interval = datetime.timedelta(minutes=args.interval)
    n_intervals = 0

    if args.vehicle_types is None:
        print("No definition of vehicle types found, using vehicles from metadata.")
        vehicle_types = parse_vehicle_types(metadata["tech_data"])
    else:
        ext = args.vehicle_types.split('.')[-1]
        if ext != "json":
            warnings.warn("File extension mismatch: vehicle type file should be '.json'.")
        with open(args.vehicle_types) as f:
            vehicle_types = json.load(f)

    def datetime_from_timestep(timestep):
        assert type(timestep) == int
        return start + (interval * timestep)

    # vehicle CSV files
    if not args.region:
        pathlist = list(simbev_path.rglob('*_events.csv'))
    else:
        region_path = Path(simbev_path, args.region)
        pathlist = list(region_path.rglob('*_events.csv'))
    pathlist.sort()

    vehicles = {}
    batteries = {}
    charging_stations = {}
    for idx, (capacity, c_rate) in enumerate(args.battery):
        if capacity > 0:
            max_power = c_rate * capacity
        else:
            # unlimited battery: set power directly
            max_power = c_rate
        batteries["BAT{}".format(idx + 1)] = {
            "parent": "GC1",
            "capacity": capacity,
            "charging_curve": [[0, max_power], [1, max_power]]
        }

    events = {
        "grid_operator_signals": [],
        "external_load": {},
        "energy_feed_in": {},
        "vehicle_events": []
    }

    # count number of trips for which desired_soc is above min_soc
    trips_above_min_soc = 0
    trips_total = 0

    # save path and options for CSV timeseries
    # all paths are relative to output file
    target_path = Path(args.output).parent

    # external load CSV
    if args.include_ext_load_csv:
        filename = args.include_ext_load_csv
        basename = Path(filename).stem
        options = {
            "csv_file": filename,
            "start_time": start.isoformat(),
            "step_duration_s": 900,  # 15 minutes
            "grid_connector_id": "GC1",
            "column": "energy"
        }
        if args.include_ext_csv_option:
            for key, value in args.include_ext_csv_option:
                if key == "step_duration_s":
                    value = int(value)
                options[key] = value
        events['external_load'][basename] = options
        # check if CSV file exists
        ext_csv_path = target_path.joinpath(filename)
        if not ext_csv_path.exists() and args.verbose > 0:
            warnings.warn(f"External csv file '{ext_csv_path}' does not exist yet.")
        else:
            with open(ext_csv_path, newline='') as csvfile:
                reader = csv.DictReader(csvfile)
                if not options["column"] in reader.fieldnames:
                    warnings.warn(f"External csv file '{ext_csv_path}' has no column "
                                  f"'{options['column']}'.")

    # energy feed-in CSV (e.g. from PV)
    if args.include_feed_in_csv:
        filename = args.include_feed_in_csv
        basename = Path(filename).stem
        options = {
            "csv_file": filename,
            "start_time": start.isoformat(),
            "step_duration_s": 3600,  # 60 minutes
            "grid_connector_id": "GC1",
            "column": "energy"
        }
        if args.include_feed_in_csv_option:
            for key, value in args.include_feed_in_csv_option:
                if key == "step_duration_s":
                    value = int(value)
                options[key] = value
        events['energy_feed_in'][basename] = options
        feed_in_path = target_path.joinpath(filename)
        if not feed_in_path.exists() and args.verbose > 0:
            warnings.warn(f"Feed-in csv file '{feed_in_path}' does not exist yet.")
        else:
            with open(feed_in_path, newline='') as csvfile:
                reader = csv.DictReader(csvfile)
                if not options["column"] in reader.fieldnames:
                    warnings.warn(f"Feed-in csv file '{feed_in_path}' has no column "
                                  f"'{options['column']}'.")

    # energy price CSV
    if args.include_price_csv:
        filename = args.include_price_csv
        # basename = Path(filename).stem
        options = {
            "csv_file": filename,
            "start_time": start.isoformat(),
            "step_duration_s": 3600,  # 60 minutes
            "grid_connector_id": "GC1",
            "column": "price [ct/kWh]"
        }
        for key, value in args.include_price_csv_option:
            if key == "step_duration_s":
                value = int(value)
            options[key] = value
        events['energy_price_from_csv'] = options
        price_csv_path = target_path.joinpath(filename)
        if not price_csv_path.exists() and args.verbose > 0:
            warnings.warn(f"Price csv file '{price_csv_path}' does not exist yet.")
        else:
            with open(price_csv_path, newline='') as csvfile:
                reader = csv.DictReader(csvfile)
                if not options["column"] in reader.fieldnames:
                    warnings.warn(f"Price csv file '{price_csv_path}' has no column "
                                  f"'{options['column']}'.")

        if args.seed and args.verbose > 0:
            # CSV and seed given
            warnings.warn("Multiple price sources detected. Using CSV.")
    elif args.seed is not None and args.seed < 0:
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

    # generate vehicle events: iterate over input files
    for csv_path in pathlist:
        # get vehicle name from file name
        vehicle_name = str(csv_path.stem)[:-7]
        if args.verbose >= 2:
            # debug
            print("Next vehicle: {}".format(csv_path))

        with open(csv_path, newline='') as csvfile:
            reader = csv.DictReader(csvfile)

            # set vehicle info from first data row
            # update in vehicles, regardless if known before or not
            v_info = vehicle_name.split("_")
            # get vehicle type from the first two parts of the csv name
            v_type = '_'.join(v_info[:2])

            # vehicle type must be known
            assert v_type in vehicle_types, f"Unknown type for {vehicle_name}: {v_type}."
            if vehicle_name in vehicles:
                num_similar_name = sum([1 for v in vehicles.keys() if v.startswith(vehicle_name)])
                vehicle_name_new = "{}_{}".format(vehicle_name, num_similar_name + 1)
                if args.verbose > 0:
                    warnings.warn(f"Vehicle name '{vehicle_name}' is not unique! "
                                  f"Renamed to '{vehicle_name_new}'.")
                vehicle_name = vehicle_name_new

            # check that capacities match
            vehicle_capacity = vehicle_types[v_type]["capacity"]
            # take capacity from vehicle file name
            file_capacity = int(v_info[-1][:-3])
            if vehicle_capacity != file_capacity:
                warnings.warn(f"Capacities of vehicle type '{v_type}' don't match!"
                              f"In file name: '{file_capacity}', in script: '{vehicle_capacity}'. "
                              "Using value from file.")
                vehicle_capacity = file_capacity
                vehicle_types[v_type]["capacity"] = file_capacity

            # set initial charge
            last_cs_event = None
            soc_needed = 0.0
            event_start_ts = None
            event_end_ts = datetime_from_timestep(0)

            # iterate next timesteps
            for idx, row in enumerate(reader):
                if idx == 0:
                    # save initial vehicle data
                    vehicle_soc = float(row["soc_start"])
                    vehicles[vehicle_name] = {
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
                    f"SimBEV created negative SoC for {vehicle_name} in row {idx + 1}."
                # might want to avoid very low battery levels (configurable in config)
                soc_threshold = args.min_soc_threshold
                if args.verbose > 0 and (
                        simbev_soc_start < soc_threshold
                        or simbev_soc_end < soc_threshold):
                    warnings.warn(f"SimBEV created very low SoC for {vehicle_name} "
                                  f"in row {idx + 1}.")

                simbev_demand = max(float(row["energy"]), 0)
                assert cs_power > 0 or simbev_demand == 0, \
                    f"Charging event without charging station: {vehicle_name} in row {idx + 1}."

                cs_present = cs_power > 0
                assert (not cs_present) or consumption == 0, \
                    f"Consumption while charging for {vehicle_name} in row {idx + 1}."

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
                            warnings.warn(f"Can't fulfill charging request for {vehicle_name} in "
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
                            f"Consumption too high for {vehicle_name} in row {idx + 1}: "
                            f"vehicle charged to {vehicle_soc}, needs SoC of {soc_needed} "
                            f"({soc_needed * vehicle_capacity} kWh). This might be caused by "
                            f"rounding differences between SimBEV and SpiceEV.")
                    else:
                        # charging station present
                        is_charge_event = True

                        if not last_cs_event:
                            # first charge: initial must be enough
                            assert vehicle_soc >= soc_needed - tolerance, (
                                f"Initial charge for {vehicle_name} is not sufficient. This might "
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
                            cs_name = last_cs_event["update"]["connected_charging_station"]
                            cs_max_power = charging_stations[cs_name]["max_power"]
                            charge_duration = event_end_ts - event_start_ts
                            possible_energy = cs_max_power * charge_duration.seconds / 3600
                            possible_soc = possible_energy / vehicle_capacity

                            if delta_soc > possible_soc and args.verbose > 0:
                                warnings.warn(
                                    f"Can't fulfill charging request for '{vehicle_name}' in ts "
                                    f"{((event_end_ts - start) / interval):.0f}. Need "
                                    f"{(desired_soc * vehicle_capacity):.2f} kWh in "
                                    f"{(charge_duration.seconds / 3600):.2f} h "
                                    f"({(charge_duration / interval):.0f} ts). "
                                    f"Possible within standing time: {possible_energy} kWh.")

                            # update last charge event info: set desired SOC
                            last_cs_event["update"]["desired_soc"] = desired_soc
                            events["vehicle_events"].append(last_cs_event)

                            # simulate charging
                            vehicle_soc = max(vehicle_soc, desired_soc)

                        # reset desired SoC for next trip
                        desired_soc = None

                        # update vehicle SOC: with how much SOC does vehicle arrive at new CS?
                        vehicle_soc -= soc_needed

                if is_charge_event:
                    # initialize new charge event

                    # setup charging point at location
                    cs_name = "{}_{}".format(vehicle_name, location)
                    if (cs_name in charging_stations
                            and charging_stations[cs_name]["max_power"] != cs_power):
                        # same location type, different cs_power: build new CS
                        cs_name = "{}_{}".format(cs_name, idx)
                    if cs_name not in charging_stations:
                        charging_stations[cs_name] = {
                            "max_power": cs_power,
                            "min_power": args.cs_power_min if args.cs_power_min else 0.1 * cs_power,
                            "parent": "GC1"
                        }

                    # generate vehicle events
                    # departure from old CS
                    event_start_idx = int(row["event_start"])
                    event_start_ts = datetime_from_timestep(event_start_idx)
                    assert event_start_ts >= event_end_ts, (
                        f"Order of vehicle {vehicle_name} wrong in timestep {event_start_idx}, "
                        f"has been standing already.")
                    if event_start_idx > 0:
                        events["vehicle_events"].append({
                            "signal_time": event_end_ts.isoformat(),
                            "start_time": event_end_ts.isoformat(),
                            "vehicle_id": vehicle_name,
                            "event_type": "departure",
                            "update": {
                                "estimated_time_of_arrival": event_start_ts.isoformat()
                            }
                        })

                    # arrival at new CS
                    event_end_idx = int(row["event_start"]) + int(row["event_time"]) + 1
                    event_end_ts = datetime_from_timestep(event_end_idx)
                    delta_soc = soc_needed if args.ignore_simbev_soc else delta_soc
                    last_cs_event = {
                        "signal_time": event_start_ts.isoformat(),
                        "start_time": event_start_ts.isoformat(),
                        "vehicle_id": vehicle_name,
                        "event_type": "arrival",
                        "update": {
                            "connected_charging_station": cs_name,
                            "estimated_time_of_departure": event_end_ts.isoformat(),
                            "desired_soc": desired_soc,  # may be None, updated later
                            "soc_delta": - delta_soc
                        }
                    }

                    if not args.ignore_simbev_soc:
                        # use SimBEV SoC: append charge event right away
                        events["vehicle_events"].append(last_cs_event)

                    # reset distance (needed charge) to next CS
                    soc_needed = 0.0

                    # get maximum length of timesteps (only end of last charge relevant)
                    n_intervals = max(n_intervals, event_end_idx)

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

    if trips_above_min_soc:
        print(f"{trips_above_min_soc} of {trips_total} trips "
              f"use more than {args.min_soc * 100}% capacity")

    assert len(vehicles) > 0, f"No vehicles found in {args.simbev}."

    # check voltage level (used in cost calculation)
    voltage_level = vars(args).get("voltage_level")
    if voltage_level is None:
        warnings.warn("Voltage level is not set, please choose one when calculating costs.")

    # create final dict
    j = {
        "scenario": {
            "start_time": start.isoformat(),
            "interval": args.interval,
            "n_intervals": n_intervals,
            "discharge_limit": args.discharge_limit,
        },
        "constants": {
            "vehicle_types": vehicle_types,
            "vehicles": vehicles,
            "grid_connectors": {
                "GC1": {
                    "max_power": vars(args).get("gc_power", 100),
                    "voltage_level": voltage_level,
                    "cost": {"type": "fixed", "value": 0.3},
                }
            },
            "charging_stations": charging_stations,
            "batteries": batteries,
            "photovoltaics": {
                "PV1": {
                    "parent": "GC1",
                    "nominal_power": vars(args).get("pv_power", 0),
                }
            },
        },
        "events": events,
    }

    # Write JSON
    with open(args.output, 'w') as f:
        json.dump(j, f, indent=2)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Generate scenarios as JSON files for vehicle charging modelling \
        from vehicle timeseries (e.g. SimBEV output).')
    parser.add_argument('output', nargs='?', help='output file name (example.json)')
    parser.add_argument('--simbev', metavar='DIR', type=str, help='set directory with SimBEV files')
    parser.add_argument('--region', type=str, help='set name of region')
    parser.add_argument('--ignore-simbev-soc', action='store_true',
                        help='Don\'t use SoC columns from SimBEV files')

    # general
    parser.add_argument('--interval', metavar='MIN', type=int, default=15,
                        help='set number of minutes for each timestep (Δt)')
    parser.add_argument('--min-soc', metavar='S', type=float, default=0.8,
                        help='Set minimum desired SoC for each charging event. Default: 0.5')
    parser.add_argument('--min-soc-threshold', type=float, default=0.05,
                        help='SoC below this threshold trigger a warning. Default: 0.05')
    parser.add_argument('--battery', '-b', default=[], nargs=2, type=float, action='append',
                        help='add battery with specified capacity in kWh and C-rate \
                            (-1 for variable capacity, second argument is fixed power))')
    parser.add_argument('--gc-power', type=int, default=100, help='set power at grid connection '
                                                                  'point in kW')
    parser.add_argument('--voltage-level', '-vl', help='Choose voltage level for cost calculation')
    parser.add_argument('--pv-power', type=int, default=0, help='set nominal power for local '
                                                                'photovoltaic power plant in kWp')
    parser.add_argument('--cs-power-min', type=float, default=None,
                        help='set minimal power at charging station in kW (default: 0.1 * cs_power')
    parser.add_argument('--discharge-limit', default=0.5,
                        help='Minimum SoC to discharge to during V2G. [0-1]')
    parser.add_argument('--seed', metavar='X', type=int, default=0,
                        help='set seed when generating energy market prices. \
                            Negative values for fixed price in cents')

    # input files (CSV, JSON)
    parser.add_argument('--vehicle-types', default=None,
                        help='location of vehicle type definitions')
    parser.add_argument('--include-ext-load-csv',
                        help='include CSV for external load. \
                        You may define custom options with --include-ext-csv-option')
    parser.add_argument('--include-ext-csv-option', '-eo', metavar=('KEY', 'VALUE'),
                        nargs=2, default=[], action='append',
                        help='append additional argument to external load')
    parser.add_argument('--include-feed-in-csv',
                        help='include CSV for energy feed-in, e.g., local PV. \
                        You may define custom options with --include-feed-in-csv-option')
    parser.add_argument('--include-feed-in-csv-option', '-fo', metavar=('KEY', 'VALUE'),
                        nargs=2, default=[], action='append',
                        help='append additional argument to feed-in load')
    parser.add_argument('--include-price-csv',
                        help='include CSV for energy price. \
                        You may define custom options with --include-price-csv-option')
    parser.add_argument('--include-price-csv-option', '-po', metavar=('KEY', 'VALUE'),
                        nargs=2, default=[], action='append',
                        help='append additional argument to price signals')

    # config
    parser.add_argument('--config', help='Use config file to set arguments')

    # other stuff
    parser.add_argument('--verbose', '-v', action='count', default=0,
                        help='Set verbosity level. Use this multiple times for more output. '
                             'Default: only errors, 1: warnings, 2: debug')

    args = parser.parse_args()

    set_options_from_config(args, check=True, verbose=args.verbose >= 2)

    generate_from_simbev(args)
