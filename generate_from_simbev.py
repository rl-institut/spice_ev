#!/usr/bin/env python3

import argparse
import csv
import datetime
import json
from pathlib import Path
import random

from src.util import set_options_from_config
from src.battery import Battery
from src.loading_curve import LoadingCurve


def parse_vehicle_types(tech_data):
    """Get vehicle data from SimBEV metadata

    :param tech_data: dictionary which containts the tech data part of a SimBEV metadata json
    :type tech_data: dict
    :return: dict
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
    :return: None

    """
    missing = [arg for arg in ["output", "simbev"] if vars(args).get(arg) is None]
    if missing:
        raise SystemExit("The following arguments are required: {}".format(", ".join(missing)))

    # read simbev metadata
    simbev_path = Path(args.simbev)
    assert simbev_path.exists(), "SimBEV directory {} does not exist".format(args.simbev)
    metadata_path = Path(simbev_path, "metadata_simbev_run.json")
    assert metadata_path.exists(), "Metadata file does not exist in SimBEV directory"
    with open(metadata_path) as f:
        metadata = json.load(f)

    # first monday of 2021
    # SimBEV uses MiD data and creates data for an exemplary week, so there are no exact dates.
    start = datetime.datetime.strptime(metadata["config"]["basic"]["start_date"], "%Y-%m-%d")
    interval = datetime.timedelta(minutes=args.interval)
    n_intervals = 0

    if args.vehicle_types is None:
        print("No definition of vehicle types found, using vehicles from metadata")
        vehicle_types = parse_vehicle_types(metadata["tech_data"])
    else:
        ext = args.vehicle_types.split('.')[-1]
        if ext != "json":
            print("File extension mismatch: vehicle type file should be .json")
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
    charging_stations = {}
    events = {
        "grid_operator_signals": [],
        "external_load": {},
        "energy_feed_in": {},
        "vehicle_events": []
    }

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
        for key, value in args.include_ext_csv_option:
            options[key] = value
        events['external_load'][basename] = options
        # check if CSV file exists
        ext_csv_path = target_path.joinpath(filename)
        if not ext_csv_path.exists() and args.verbose > 0:
            print("Warning: external csv file '{}' does not exist yet".format(ext_csv_path))
        else:
            with open(ext_csv_path, newline='') as csvfile:
                reader = csv.DictReader(csvfile)
                if not options["column"] in reader.fieldnames:
                    print("Warning: external csv file {} has no column {}".format(
                          ext_csv_path, options["column"]))

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
        for key, value in args.include_feed_in_csv_option:
            options[key] = value
        events['energy_feed_in'][basename] = options
        feed_in_path = target_path.joinpath(filename)
        if not feed_in_path.exists() and args.verbose > 0:
            print("Warning: feed-in csv file '{}' does not exist yet".format(feed_in_path))
        else:
            with open(feed_in_path, newline='') as csvfile:
                reader = csv.DictReader(csvfile)
                if not options["column"] in reader.fieldnames:
                    print("Warning: feed-in csv file {} has no column {}".format(
                          feed_in_path, options["column"]))

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
            options[key] = value
        events['energy_price_from_csv'] = options
        price_csv_path = target_path.joinpath(filename)
        if not price_csv_path.exists() and args.verbose > 0:
            print("Warning: price csv file '{}' does not exist yet".format(price_csv_path))
        else:
            with open(price_csv_path, newline='') as csvfile:
                reader = csv.DictReader(csvfile)
                if not options["column"] in reader.fieldnames:
                    print("Warning: price csv file {} has no column {}".format(
                          price_csv_path, options["column"]))

        if args.price_seed and args.verbose > 0:
            # CSV and price_seed given
            print("WARNING: Multiple price sources detected. Using CSV.")
    elif args.price_seed is not None and args.price_seed < 0:
        # use single, fixed price
        events["grid_operator_signals"].append({
            "signal_time": start.isoformat(),
            "grid_connector_id": "GC1",
            "start_time": start.isoformat(),
            "cost": {
                "type": "fixed",
                "value": -args.price_seed
            }
        })
    else:
        # random price
        # set seed from input (repeatability)
        random.seed(args.price_seed)
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
            assert v_type in vehicle_types, "Unknown type for {}: {}".format(vehicle_name, v_type)
            if vehicle_name in vehicles:
                num_similar_name = sum([1 for v in vehicles.keys() if v.startswith(vehicle_name)])
                vehicle_name_new = "{}_{}".format(vehicle_name, num_similar_name + 1)
                if args.verbose > 0:
                    print("WARNING: Vehicle name {} is not unique! "
                          "Renamed to {}".format(vehicle_name, vehicle_name_new))
                vehicle_name = vehicle_name_new

            # check that capacities match
            vehicle_capacity = vehicle_types[v_type]["capacity"]
            # take capacity from vehicle file name
            file_capacity = int(v_info[-1][:-3])
            if vehicle_capacity != file_capacity:
                print("WARNING: capacities of car type {} don't match "
                      "(in file name: {}, in script: {}). Using value from file.".
                      format(v_type, file_capacity, vehicle_capacity))
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
                capacity = float(row["station_charging_capacity"])
                consumption = abs(min(float(row["energy"]), 0))

                # general sanity checks
                simbev_soc_start = float(row["soc_start"])
                simbev_soc_end = float(row["soc_end"])
                # SoC must not be negative
                assert simbev_soc_start >= 0 and simbev_soc_end >= 0, \
                    "SimBEV created negative SoC for {} in row {}".format(
                        vehicle_name, idx + 3)
                # might want to avoid very low battery levels (configurable in config)
                soc_threshold = args.min_soc_threshold
                if args.verbose > 0 and (
                        simbev_soc_start < soc_threshold
                        or simbev_soc_end < soc_threshold):
                    print("WARNING: SimBEV created very low SoC for {} in row {}"
                          .format(vehicle_name, idx + 3))

                simbev_demand = max(float(row["energy"]), 0)
                assert capacity > 0 or simbev_demand == 0, \
                    "Charging event without charging station: {} @ row {}".format(
                        vehicle_name, idx + 3)

                cs_present = capacity > 0
                assert (not cs_present) or consumption == 0, \
                    "Consumption while charging for {} @ row {}".format(
                        vehicle_name, idx + 3)

                # actual driving and charging behavior
                if args.use_simbev_soc:
                    if cs_present and float(row["energy"]) > 0:
                        # arrival at new CS: use info from SimBEV directly
                        is_charge_event = True
                        desired_soc = float(row["soc_end"])
                        delta_soc = (vehicle_soc - float(row["soc_start"]))

                        # check if feasible: simulate with battery
                        # set battery SoC to level when arriving
                        battery.soc = float(row["soc_start"])
                        charge_duration = int(row["event_time"]) * interval
                        battery.load(charge_duration, capacity)
                        if battery.soc < float(row["soc_end"]) and args.verbose > 0:
                            print("WARNING: Can't fulfill charging request for {} in ts {}. "
                                  "Desired SoC is set to {:.3f}, possible: {:.3f}"
                                  .format(
                                    vehicle_name, row["timestamp"],
                                    desired_soc, battery.soc
                                  ))
                        vehicle_soc = desired_soc
                else:
                    # compute needed power and desired SoC independent of SimBEV
                    if not cs_present:
                        # no charging station or don't need to charge
                        # just increase charging demand based on consumption
                        soc_needed += consumption / vehicle_capacity
                        assert soc_needed <= 1 + vehicle_soc + args.eps, (
                            "Consumption too high for {} in row {}: "
                            "vehicle charged to {}, needs SoC of {} ({} kWh). "
                            "This might be caused by rounding differences, "
                            "consider to increase the arg '--eps'.".format(
                                vehicle_name, idx + 3, vehicle_soc,
                                soc_needed, soc_needed * vehicle_capacity))
                    else:
                        # charging station present
                        is_charge_event = True

                        if not last_cs_event:
                            # first charge: initial must be enough
                            assert vehicle_soc >= soc_needed - args.eps, (
                                "Initial charge for {} is not sufficient. "
                                "This might be caused by rounding differences, "
                                "consider to increase the arg '--eps'.".format(
                                    vehicle_name))
                        else:
                            # update desired SoC from last charging event
                            # this much charge must be in battery when leaving CS
                            # to reach next CS (the one from current row)
                            desired_soc = max(args.min_soc, soc_needed)

                            # this much must be charged
                            delta_soc = max(desired_soc - vehicle_soc, 0)

                            # check if charging is possible in ideal case
                            cs_name = last_cs_event["update"]["connected_charging_station"]
                            cs_power = charging_stations[cs_name]["max_power"]
                            charge_duration = event_end_ts - event_start_ts
                            possible_power = cs_power * charge_duration.seconds/3600
                            possible_soc = possible_power / vehicle_capacity

                            if delta_soc > possible_soc and args.verbose > 0:
                                print(
                                    "WARNING: Can't fulfill charging request for {} in ts {:.0f}. "
                                    "Need {:.2f} kWh in {:.2f} h ({:.0f} ts) from {} kW CS, "
                                    "possible: {} kWh"
                                    .format(
                                        vehicle_name,
                                        (event_end_ts - start)/interval,
                                        desired_soc * vehicle_capacity,
                                        charge_duration.seconds/3600,
                                        charge_duration / interval,
                                        cs_power, possible_power
                                    ))

                            # update last charge event info: set desired SOC
                            last_cs_event["update"]["desired_soc"] = desired_soc
                            events["vehicle_events"].append(last_cs_event)

                            # simulate charging
                            vehicle_soc = max(vehicle_soc, desired_soc)

                        # reset desired SoC for next trip
                        desired_soc = None

                        # update vehicle SOC: with how much SOC does car arrive at new CS?
                        vehicle_soc -= soc_needed

                if is_charge_event:
                    # initialize new charge event

                    # setup charging point at location
                    cs_name = "{}_{}".format(vehicle_name, location)
                    if (cs_name in charging_stations
                            and charging_stations[cs_name]["max_power"] != capacity):
                        # same location type, different capacity: build new CS
                        cs_name = "{}_{}".format(cs_name, idx)
                    if cs_name not in charging_stations:
                        charging_stations[cs_name] = {
                            # get max power from charging curve
                            "max_power": capacity,
                            "parent": "GC1",
                            "min_power": capacity * 0.1,
                        }

                    # generate vehicle events
                    # departure from old CS
                    event_start_idx = int(row["event_start"])
                    event_start_ts = datetime_from_timestep(event_start_idx)
                    assert event_start_ts >= event_end_ts, (
                        "Order of vehicle {} wrong in timestep {}, has been standing already"
                    ).format(vehicle_name, event_start_idx)
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
                    delta_soc = delta_soc if args.use_simbev_soc else soc_needed
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

                    if args.use_simbev_soc:
                        # append charge event right away
                        events["vehicle_events"].append(last_cs_event)

                    # reset distance (needed charge) to next CS
                    soc_needed = 0.0

                    # get maximum length of timesteps (only end of last charge relevant)
                    n_intervals = max(n_intervals, event_end_idx)

                    # random price: each price interval, generate new price

                    while (
                        not args.include_price_csv
                        and (args.price_seed is None or args.price_seed >= 0)
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

    assert len(vehicles) > 0, "No vehicles found in {}".format(args.simbev)

    j = {
        "scenario": {
            "start_time": start.isoformat(),
            "interval": args.interval,
            "n_intervals": n_intervals,
        },
        "constants": {
            "vehicle_types": vehicle_types,
            "vehicles": vehicles,
            "grid_connectors": {
                "GC1": {
                    "max_power": 10000
                }
            },
            "charging_stations": charging_stations
        },
        "events": events
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
    parser.add_argument('--interval', metavar='MIN', type=int, default=15,
                        help='set number of minutes for each timestep (Δt)')
    parser.add_argument('--price-seed', metavar='X', type=int, default=0,
                        help='set seed when generating energy market prices. \
                        Negative values for fixed price in cents')
    parser.add_argument('--min-soc', metavar='S', type=float, default=0.5,
                        help='Set minimum desired SoC for each charging event. Default: 0.5')
    parser.add_argument('--min-soc-threshold', type=float, default=0.05,
                        help='SoC below this threshold trigger a warning. Default: 0.05')
    parser.add_argument('--use-simbev-soc', action='store_true',
                        help='Use SoC columns from SimBEV files')
    parser.add_argument('--verbose', '-v', action='count', default=0,
                        help='Set verbosity level. Use this multiple times for more output. '
                             'Default: only errors, 1: warnings, 2: debug')

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
    parser.add_argument('--config', help='Use config file to set arguments')

    # other stuff
    parser.add_argument('--eps', metavar='EPS', type=float, default=1e-10,
                        help='Tolerance used for sanity checks, required due to possible '
                             'rounding differences between simBEV and spiceEV. Default: 1e-10')

    args = parser.parse_args()

    set_options_from_config(args, check=True, verbose=args.verbose >= 2)

    generate_from_simbev(args)
