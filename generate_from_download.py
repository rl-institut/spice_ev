#!/usr/bin/env python3

import argparse
import csv
import datetime
import json
from pathlib import Path  # used to check if given CSV files exist

from src.util import set_options_from_config


def generate_from_download(args):
    """Generate a scenario JSON from JSON file with LIS event data.

    :param args: input arguments
    :type args: argparse.Namespace
    :return: None
    """
    missing = [arg for arg in ["input", "output", "car_allocation"] if vars(args).get(arg) is None]
    if missing:
        raise SystemExit("The following arguments are required: {}".format(", ".join(missing)))

    with open(args.input, 'r') as f:
        input_json = json.load(f)

    # read in vehicle types
    if args.vehicle_types is None:
        args.vehicle_types = "examples/vehicle_types.json"
        print("No definition of vehicle types found, using {}".format(args.vehicle_types))
    ext = args.vehicle_types.split('.')[-1]
    if ext != "json":
        print("File extension mismatch: vehicle type file should be .json")
    with open(args.vehicle_types) as f:
        vehicle_types = json.load(f)
    # build look-up table vehicle capacity -> vehicle type
    v_cap_to_type = {}
    for t, v in vehicle_types.items():
        c = v["capacity"]
        if c in v_cap_to_type:
            v_cap_to_type[c].append(t)
        else:
            v_cap_to_type[c] = [t]
    vehicle_types_present = {}

    # read in vehicle allocation file
    vehicles = {}
    charging_stations = {}
    with open(args.car_allocation, newline='') as f:
        reader = csv.DictReader(f, fieldnames=['gate', 'cp', 'akz', 'capacity'])
        # skip header
        next(reader)
        for row in reader:
            # find suitable vehicle type
            capacity = float(row["capacity"])
            if capacity not in v_cap_to_type:
                # unknown capacity
                raise ValueError("Given capacity {} not in vehicle types {}"
                                 .format(capacity, args.vehicle_types))
            vtype = v_cap_to_type[capacity]
            if len(vtype) > 1:
                # ambiguous vehicle type
                raise ValueError("Given capacity {} not unique in vehicle types {}, could be {}"
                                 .format(capacity, args.vehicle_types, ' or '.join(vtype)))

            # unique capacity: add type to present vehicle types, increase counter
            vtype = vtype[0]
            if vtype not in vehicle_types_present:
                vehicle_types_present[vtype] = vehicle_types[vtype]
                vehicle_types_present[vtype]["count"] = 0
            vehicle_types_present[vtype]["count"] += 1
            count = vehicle_types_present[vtype]["count"]

            # add new vehicle of type
            vname = f"{vtype}_{count}"
            vehicles[vname] = {
                "soc": args.min_soc,
                "vehicle_type": vtype,
                "last_departure": None,
            }

            # add charging station
            charging_stations[f"cp{row['gate']}"] = {
                "max_power": 11,
                "min_power": 0.2,
                "parent": "GC1",
                "vehicle": vname,
            }

    events = {
        "grid_operator_signals": [],
        "external_load": {},
        "energy_feed_in": {},
        "vehicle_events": []
    }
    events_ignored = []

    start_time = None
    stop_time = None

    for event in sorted(input_json, key=lambda e: e["meterStartDate"]):
        # charging station
        cs_id = event["evseId"]
        try:
            cs = charging_stations[cs_id]
        except KeyError:
            raise KeyError("Unknown charging station {}, check allocation file".format(cs_id))

        # process charge event
        arrival_time = datetime.datetime.fromtimestamp(event["meterStartDate"] / 1000)
        departure_time = datetime.datetime.fromtimestamp(event["meterStopDate"] / 1000)
        # make times timezone-aware (from Unix timestamp -> UTC)
        arrival_time = arrival_time.replace(tzinfo=datetime.timezone.utc)
        departure_time = departure_time.replace(tzinfo=datetime.timezone.utc)
        # update scenario time bounds
        start_time = min(start_time, arrival_time) if start_time else arrival_time
        stop_time = max(stop_time, departure_time) if stop_time else departure_time

        v_id = cs["vehicle"]
        vehicle = vehicles[v_id]
        last_departure = vehicle["last_departure"]
        if last_departure is not None and last_departure > arrival_time:
            raise RuntimeError("Vehicle {} arrives before departing (transaction ID {})"
                               .format(v_id, event["transactionId"]))

        # compute energy and SoC used
        energy_used = event["usage"]/1000
        soc_delta = energy_used / capacity

        if soc_delta > args.min_soc:
            # not enough minimum SoC to make the trip
            print("WARNING: minimum SoC too low, need at least {} (see transaction {})".format(
                soc_delta, event["transactionId"]))

        # ignore minimal charging (probably faulty event)
        if energy_used >= 0.1 and event["status"] == "completed_tx":

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
        else:
            # less than 1 kWh used or different reason or not finished: dummy /faulty event
            events_ignored.append(event["transactionId"])
        vehicle["last_departure"] = departure_time

    # set start time to midnight (most CSV start at midnight)
    start_time = start_time.replace(hour=0, minute=0, second=0, microsecond=0)

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

    # clean up dictionaries before writing
    for v in vehicles.values():
        del v["last_departure"]
    # vehicle_types.count and charging_stations.vehicle are superfluous as well
    # but might be interesting for debugging

    # gather all information in one dictionary
    j = {
        "scenario": {
            "start_time": start_time.isoformat(),
            "stop_time": stop_time.isoformat(),
            "interval": args.interval,
        },
        "constants": {
            "vehicle_types": vehicle_types_present,
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

    if events_ignored:
        print(f"{len(events_ignored)} / {len(input_json)} events ignored: {events_ignored}")

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
    parser.add_argument('--gc-power', metavar='P', type=float, default=530,
                        help='set maximum power of grid connector')
    parser.add_argument('--battery', '-b', metavar=('CAP', 'C-RATE'),
                        default=[], nargs=2, type=float, action='append',
                        help='add battery with specified capacity in kWh and C-rate \
                        (-1 for variable capacity, second argument is fixed power))')

    # input files (CSV, JSON)
    parser.add_argument('--vehicle-types', default=None,
                        help='location of vehicle type definitions')
    parser.add_argument('--car-allocation', '-calloc', default=None,
                        help='location of gate to vehicle allocation file')
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
