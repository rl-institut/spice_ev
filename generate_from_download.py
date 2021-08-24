#!/usr/bin/env python3

import argparse
import csv  # used to check if columns exists in given CSV files
import datetime
import json
from pathlib import Path  # used to check if given CSV files exist

from src.util import set_options_from_config


def generate_from_download(args):
    """Generate a scenario JSON from JSON file with LIS event data.
    args: argparse.Namespace
    """
    missing = [arg for arg in ["input", "output"] if vars(args).get(arg) is None]
    if missing:
        raise SystemExit("The following arguments are required: {}".format(", ".join(missing)))

    with open(args.input, 'r') as f:
        input_json = json.load(f)

    # VEHICLE TYPES
    CAPACITY = 76  # kWh

    vehicle_types = {
        "sprinter": {
            "name": "sprinter",
            "capacity": CAPACITY,
            "mileage": 40,  # kWh/100km
            "charging_curve": [[0, 11], [0.8, 11], [1, 11]],  # kW
            "min_charging_power": 0,  # kW
            "v2g": args.v2g,
            "count": 20
        },
    }

    vehicles = {
        "sprinter_{}".format(i+1): {
            "soc": args.min_soc,
            "vehicle_type": "sprinter"
        } for i in range(vehicle_types["sprinter"]["count"])
    }

    vehicle_queue = list(zip(vehicles.keys(), [None]*len(vehicles)))

    charging_stations = {}
    events = {
        "grid_operator_signals": [],
        "external_load": {},
        "energy_feed_in": {},
        "vehicle_events": []
    }

    start_time = None
    stop_time = None

    for event in sorted(input_json, key=lambda e: e["meterStartDate"]):
        # charging station
        cs_id = event["evseId"]
        if cs_id not in charging_stations:
            charging_stations[cs_id] = {
                "max_power": 11,
                "min_power": 0.2,
                "parent": "GC1"
            }

        # process charge event
        arrival_time = datetime.datetime.fromtimestamp(event["meterStartDate"] / 1000)
        departure_time = datetime.datetime.fromtimestamp(event["meterStopDate"] / 1000)
        # make times timezone-aware (from Unix timestamp -> UTC)
        arrival_time = arrival_time.replace(tzinfo=datetime.timezone.utc)
        departure_time = departure_time.replace(tzinfo=datetime.timezone.utc)
        # update scenario time bounds
        start_time = min(start_time, arrival_time) if start_time else arrival_time
        stop_time = max(stop_time, departure_time) if stop_time else departure_time

        # take next vehicle (longest driving)
        v_id, last_departure = vehicle_queue.pop(0)
        if last_departure is not None and last_departure > arrival_time:
            raise RuntimeError("Not enough vehicles (transaction {})".format(
                event["transactionId"]))

        # compute energy / SoC used
        energy_used = event["usage"]/1000
        soc_delta = energy_used / CAPACITY

        if soc_delta > args.min_soc:
            # not enough minimum SoC to make the trip
            print("WARNING: minimum SoC too low, need at least {} (see transaction {})".format(
                soc_delta, event["transactionId"]))
        if energy_used > 1:
            # less than 1 kWh used: dummy /faulty event

            # generate events
            events["vehicle_events"].append({
                "signal_time": arrival_time.isoformat(),
                "start_time": arrival_time.isoformat(),
                "vehicle_id": v_id,
                "event_type": "arrival",
                "update": {
                    "connected_charging_station": cs_id,
                    "estimated_time_of_departure": departure_time.isoformat(),
                    "desired_soc": args.min_soc,
                    "soc_delta": -soc_delta
                }
            })
            events["vehicle_events"].append({
                "signal_time": departure_time.isoformat(),
                "start_time": departure_time.isoformat(),
                "vehicle_id": v_id,
                "event_type": "departure",
                "update": {
                    "estimated_time_of_arrival": None
                }
            })

        # insert vehicles back into vehicle queue
        # sort by departure
        for idx, (_, dep_time) in enumerate(vehicle_queue):
            if dep_time is not None and departure_time < dep_time:
                vehicle_queue.insert(idx, (v_id, departure_time))
                break
        else:
            vehicle_queue.append((v_id, departure_time))

    # align start_time to next interval
    interval = datetime.timedelta(minutes=args.interval)
    min_datetime = datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
    start_time = start_time + (min_datetime - start_time) % interval

    # save path and options for CSV timeseries
    # all paths are relative to output file
    target_path = Path(args.output).parent

    # external load CSV
    if args.include_ext_load_csv:
        filename = args.include_ext_load_csv
        basename = filename.split('.')[0]
        options = {
            "csv_file": filename,
            "start_time": start_time.isoformat(),
            "step_duration_s": 900,  # 15 minutes
            "grid_connector_id": "GC1",
            "column": "energy"
        }
        for key, value in args.include_ext_csv_option:
            options[key] = value
        events['external_load'][basename] = options
        # check if CSV file exists
        ext_csv_path = target_path.joinpath(filename)
        if not ext_csv_path.exists():
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
        basename = filename.split('.')[0]
        options = {
            "csv_file": filename,
            "start_time": start_time.isoformat(),
            "step_duration_s": 3600,  # 60 minutes
            "grid_connector_id": "GC1",
            "column": "energy"
        }
        for key, value in args.include_feed_in_csv_option:
            options[key] = value
        events['energy_feed_in'][basename] = options
        feed_in_path = target_path.joinpath(filename)
        if not feed_in_path.exists():
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
        basename = filename.split('.')[0]
        options = {
            "csv_file": filename,
            "start_time": start_time.isoformat(),
            "step_duration_s": 21600,  # 6 hours
            "grid_connector_id": "GC1",
            "column": "price [ct/kWh]"
        }
        for key, value in args.include_price_csv_option:
            options[key] = value
        events['energy_price_from_csv'] = options
        price_csv_path = target_path.joinpath(filename)
        if not price_csv_path.exists():
            print("Warning: price csv file '{}' does not exist yet".format(price_csv_path))
        else:
            with open(price_csv_path, newline='') as csvfile:
                reader = csv.DictReader(csvfile)
                if not options["column"] in reader.fieldnames:
                    print("Warning: price csv file {} has no column {}".format(
                          price_csv_path, options["column"]))

    # stationary batteries
    batteries = {}
    for idx, (capacity, c_rate) in enumerate(args.battery):
        if capacity > 0:
            max_power = c_rate * capacity
        else:
            # unlimited battery: set power directly
            max_power = c_rate
        batteries["BAT{}".format(idx+1)] = {
            "parent": "GC1",
            "capacity": capacity,
            "charging_curve": [[0, max_power], [1, max_power]]
        }

    # gather all information in one dictionary
    j = {
        "scenario": {
            "start_time": start_time.isoformat(),
            "stop_time": stop_time.isoformat(),
            "interval": args.interval,
        },
        "constants": {
            "vehicle_types": vehicle_types,
            "vehicles": vehicles,
            "grid_connectors": {
                "GC1": {
                    "max_power": args.gc_power,
                    "cost": {"type": "fixed", "value": 0.3}
                }
            },
            "charging_stations": charging_stations,
            "batteries": batteries
        },
        "events": events
    }

    # Write JSON
    with open(args.output, 'w') as f:
        json.dump(j, f, indent=2)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Generate a JSON scenario file from downloaded JSON file with LIS event data')
    parser.add_argument('input', nargs='?', help='input file name (response.json)')
    parser.add_argument('output', nargs='?', help='output file name (example.json)')
    parser.add_argument('--interval', metavar='MIN', type=int, default=15,
                        help='set number of minutes for each timestep (Δt)')
    parser.add_argument('--min-soc', metavar='SOC', type=float, default=1,
                        help='set minimum desired SOC (0 - 1) for each charging process')
    parser.add_argument('--v2g', action='store_true',
                        help='Vehicles have vehicle-to-grid capability')
    parser.add_argument('--gc-power', metavar='P', type=float, default=630,
                        help='set maximum power of grid connector')
    parser.add_argument('--battery', '-b', metavar=('CAP', 'C-RATE'),
                        default=[], nargs=2, type=float, action='append',
                        help='add battery with specified capacity in kWh and C-rate \
                        (-1 for variable capacity, second argument is fixed power))')

    # csv files
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
    args = parser.parse_args()
    set_options_from_config(args, check=True, verbose=False)
    generate_from_download(args)
