#!/usr/bin/env python3

import argparse
import csv
import datetime
import json
from os import path
import random

from src.util import set_options_from_config


def generate_from_csv(args):
    """Generate a scenario JSON from csv rotation schedule of fleets.
    args: argparse.Namespace
    """
    missing = [arg for arg in ["input_file", "output"] if vars(args).get(arg) is None]
    if missing:
        raise SystemExit("The following arguments are required: {}".format(", ".join(missing)))

    # arguments:
    interval = datetime.timedelta(minutes=args.interval)
    # read csv input file
    input = csv_to_dict(args.input_file)

    # VEHICLES
    if args.vehicle_types is None:
        args.vehicle_types = "examples/vehicle_types.json"
        print("No definition of vehicle types found, using {}".format(args.vehicle_types))
    ext = args.vehicle_types.split('.')[-1]
    if ext != "json":
        print("File extension mismatch: vehicle type file should be .json")
    with open(args.vehicle_types) as f:
        predefined_vehicle_types = json.load(f)

    for row in input:
        row["vehicle_type"] = row["vehicle_id"].split('_')[0]

    number_vehicles_per_type = get_number_busses_per_bustype(input)
    vehicle_types = {}
    vehicles = {}
    batteries = {}
    charging_stations = {}
    events = {
        "grid_operator_signals": [],
        "external_load": {},
        "energy_feed_in": {},
        "vehicle_events": []
    }
    for vehicle_type in number_vehicles_per_type:
        # update vehicle types with vehicles in input csv
        try:
            vehicle_types.update({vehicle_type: predefined_vehicle_types[vehicle_type]})
        except KeyError:
            print(f"The vehicle type {vehicle_type} defined in the input csv cannot be found in "
                  f"vehicle_types.json. Please check for consistency.")

    for bus_type in number_vehicles_per_type.keys():
        for i in range(1, number_vehicles_per_type[bus_type]+1):
            name = bus_type
            v_name = "{}_{}".format(name, i)
            cs_name = "CS_" + v_name
            # define start conditions
            vehicles[v_name] = {
                "connected_charging_station": None,
                "estimated_time_of_departure": None,
                "desired_soc": args.min_soc,
                "soc": 1,
                "vehicle_type": name
            }
            t = vehicle_types[name]
            cs_power = max([v[1] for v in t['charging_curve']])
            charging_stations[cs_name] = {
                "max_power": cs_power,
                "min_power": 0.1 * cs_power,
                "parent": "GC1"
            }

            # filter all rides for that bus
            vid_list = []
            [vid_list.append(row) for row in input if (row["vehicle_id"] == v_name)]

            # check if each bus is only used once a day
            list_vehicle_days = [d["day"] for d in vid_list]
            count_v_per_day = {i: list_vehicle_days.count(i) for i in list_vehicle_days}
            if any(v > 1 for v in count_v_per_day.values()):
                raise ValueError
            # sort events
            vid_list = sorted(vid_list, key=lambda x: x["departure time"])
            for index, row in enumerate(vid_list):
                departure_event_in_input = True
                arrival = row["arrival time"]
                arrival = datetime.datetime.strptime(arrival, '%Y-%m-%d %H:%M:%S')
                try:
                    departure = vid_list[index+1]["departure time"]
                    departure = datetime.datetime.strptime(departure, '%Y-%m-%d %H:%M:%S')
                    next_arrival = vid_list[index+1]["arrival time"]
                    next_arrival = datetime.datetime.strptime(next_arrival, '%Y-%m-%d %H:%M:%S')
                except IndexError:
                    departure_event_in_input = False
                    departure = arrival + datetime.timedelta(hours=8)

                events["vehicle_events"].append({
                    "signal_time": arrival.isoformat(),
                    "start_time": arrival.isoformat(),
                    "vehicle_id": v_name,
                    "event_type": "arrival",
                    "update": {
                        "connected_charging_station": "CS_" + v_name,
                        "estimated_time_of_departure": departure.isoformat(),
                        "soc": float(row["soc"])/100,
                        "soc_delta": ((100 - float(row["soc"])) / 100) * (-1)
                    }
                })
                if departure_event_in_input:
                    events["vehicle_events"].append({
                        "signal_time": departure.isoformat(),
                        "start_time": departure.isoformat(),
                        "vehicle_id": v_name,
                        "event_type": "departure",
                        "update": {
                            "estimated_time_of_arrival":  next_arrival.isoformat()
                        }
                    })

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
    # save path and options for CSV timeseries
    # all paths are relative to output file
    target_path = path.dirname(args.output)
    times = []
    for row in input:
        times.append(row["departure time"])
    times.sort()
    start = times[0]
    start = datetime.datetime.strptime(start, '%Y-%m-%d %H:%M:%S')
    stop = start + datetime.timedelta(days=args.days)

    if args.include_ext_load_csv:
        filename = args.include_ext_load_csv
        basename = path.splitext(path.basename(filename))[0]
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
        ext_csv_path = path.join(target_path, filename)
        if not path.exists(ext_csv_path):
            print(
                "Warning: external csv file '{}' does not exist yet".format(
                    ext_csv_path))

    if args.include_feed_in_csv:
        filename = args.include_feed_in_csv
        basename = path.splitext(path.basename(filename))[0]
        options = {
            "csv_file": filename,
            "start_time": start.astimezone().replace(microsecond=0).isoformat(),
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
        feed_in_path = path.join(target_path, filename)
        if not path.exists(feed_in_path):
            print(
                "Warning: feed-in csv file '{}' does not exist yet".format(
                    feed_in_path))

    if args.include_price_csv:
        filename = args.include_price_csv
        # basename = path.splitext(path.basename(filename))[0]
        options = {
            "csv_file": filename,
            "start_time": start.astimezone().replace(microsecond=0).isoformat(),
            "step_duration_s": 3600,  # 60 minutes
            "grid_connector_id": "GC1",
            "column": "price [ct/kWh]"
        }
        for key, value in args.include_price_csv_option:
            if key == "step_duration_s":
                value = int(value)
            options[key] = value
        events['energy_price_from_csv'] = options
        price_csv_path = path.join(target_path, filename)
        if not path.exists(price_csv_path):
            print("Warning: price csv file '{}' does not exist yet".format(
                price_csv_path))

    daily = datetime.timedelta(days=1)
    # price events
    now = start - daily
    while now < stop + 2 * daily:
        now += daily
        for v_id, v in vehicles.items():
            if now >= stop:
                # after end of scenario: keep generating trips, but don't include in scenario
                continue

        # generate prices for the day
        if not args.include_price_csv and now < stop:
            morning = now + datetime.timedelta(hours=6)
            evening_by_month = now + datetime.timedelta(hours=22 - abs(6 - now.month))
            events['grid_operator_signals'] += [{
                # day (6-evening): 15ct
                "signal_time": max(start, now - daily).isoformat(),
                "grid_connector_id": "GC1",
                "start_time": morning.isoformat(),
                "cost": {
                    "type": "fixed",
                    "value": 0.15 + random.gauss(0, 0.05)
                }
            }, {
                # night (depending on month - 6): 5ct
                "signal_time": max(start, now - daily).isoformat(),
                "grid_connector_id": "GC1",
                "start_time": evening_by_month.isoformat(),
                "cost": {
                    "type": "fixed",
                    "value": 0.05 + random.gauss(0, 0.03)
                }
            }]
    # create final dict
    j = {
        "scenario": {
            "start_time": start.isoformat(),
            "interval": interval.days * 24 * 60 + interval.seconds // 60,
            "n_intervals": (stop - start) // interval
        },
        "constants": {
            "vehicle_types": vehicle_types,
            "vehicles": vehicles,
            "grid_connectors": {
                "GC1": {
                    "max_power": vars(args).get("gc_power", 530),
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


def get_number_busses_per_bustype(dict):

    type = {}
    list_vt = []
    for row in dict:
        list_vt.append(row["vehicle_type"])
    count_vt = {i: list_vt.count(i) for i in list_vt}

    for vehicle_type in count_vt:
        type[vehicle_type] = list()
    # sort arrival times
    for day in range(1, 8):
        df_day = []
        for row in dict:
            if row["day"] == str(day):
                df_day.append(row)
        for bus_type in count_vt:
            type_count = 0
            for row in df_day:
                if row["vehicle_type"] == bus_type:
                    type_count += 1
            type[bus_type].append(type_count)

    for bus_type in type.keys():
        type[bus_type] = max(type[bus_type])
    return type


def csv_to_dict(csv_path, headers=True):

    dict = []
    with open(csv_path, 'r') as file:
        reader = csv.reader(file)
        # set column names using first row
        if headers:
            columns = next(reader)

        # convert csv to json
        for row in reader:
            row_data = {}
            for i in range(len(row)):
                # set key names
                if headers:
                    row_key = columns[i].lower()
                else:
                    row_key = i
                # set key/value
                row_data[row_key] = row[i]
            # add data to json store
            dict.append(row_data)
    return dict


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Generate scenarios as JSON files for vehicle charging modelling')
    parser.add_argument('output', nargs='?', help='output file name (example.json)')
    parser.add_argument('--capacities', metavar=('N', 'TYPE'), nargs=2, action='append', type=str,
                        help='set number of cars for a vehicle type, \
                        e.g. `--cars 100 sprinter` or `--cars 13 golf`')
    parser.add_argument('--days', metavar='N', type=int, default=30,
                        help='set duration of scenario as number of days')
    parser.add_argument('--interval', metavar='MIN', type=int, default=15,
                        help='set number of minutes for each timestep (Δt)')
    parser.add_argument('--min-soc', metavar='SOC', type=float, default=0.8,
                        help='set minimum desired SOC (0 - 1) for each charging process')
    parser.add_argument('--battery', '-b', default=[], nargs=2, type=float, action='append',
                        help='add battery with specified capacity in kWh and C-rate \
                        (-1 for variable capacity, second argument is fixed power))')
    parser.add_argument('--seed', default=None, type=int, help='set random seed')
    parser.add_argument('--include-ext-load-csv',
                        help='include CSV for external load. \
                        You may define custom options with --include-ext-csv-option')
    parser.add_argument('--include-ext-csv-option', '-eo', metavar=('KEY', 'VALUE'),
                        nargs=2, action='append',
                        help='append additional argument to external load')
    parser.add_argument('--include-feed-in-csv',
                        help='include CSV for energy feed-in, e.g., local PV. \
                        You may define custom options with --include-feed-in-csv-option')
    parser.add_argument('--include-feed-in-csv-option', '-fo', metavar=('KEY', 'VALUE'),
                        nargs=2, action='append', help='append additional argument to feed-in load')
    parser.add_argument('--include-price-csv',
                        help='include CSV for energy price. \
                        You may define custom options with --include-price-csv-option')
    parser.add_argument('--include-price-csv-option', '-po', metavar=('KEY', 'VALUE'),
                        nargs=2, default=[], action='append',
                        help='append additional argument to price signals')
    parser.add_argument('--config', help='Use config file to set arguments',
                        default='examples/generate_from_csv.cfg')

    args = parser.parse_args()

    set_options_from_config(args, check=False, verbose=False)

    generate_from_csv(args)
