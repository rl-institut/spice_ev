#!/usr/bin/env python3

import argparse
import datetime
import json
import random
import warnings
from os import path

from src.util import set_options_from_config, datetime_from_isoformat


def datetime_from_string(s):
    h, m = map(int, s.split(':'))
    return datetime.datetime(1972, 1, 1, h, m)


def generate_trip(args):
    """
    Creates randomly generated dummy trips from average input arguments

    :param args: input arguments
    :type args: argparse.Namespace
    :return:
        start.time(), stop.time(), distance
    """
    # distance of one trip
    avg_distance = vars(args).get("avg_distance", 47.13)  # km
    std_distance = vars(args).get("std_distance", 19.89)
    min_distance = vars(args).get("min_distance", 27.23)
    max_distance = vars(args).get("max_distance", 67.02)
    # departure time
    avg_start = vars(args).get("avg_start", "08:15")  # hh:mm
    std_start = vars(args).get("std_start", 0.42)  # hours
    min_start = vars(args).get("min_start", "07:00")
    max_start = vars(args).get("max_start", "09:30")
    # trip duration
    avg_driving = vars(args).get("avg_driving", 8.33)  # hours
    std_driving = vars(args).get("std_driving", 1.35)
    min_driving = vars(args).get("min_driving", 4.28)
    max_driving = vars(args).get("max_driving", 12.38)

    # start time
    start = datetime_from_string(avg_start)
    # to timestamp (resolution in seconds)
    start = start.timestamp()
    # apply normal distribution (hours -> seconds)
    start = random.gauss(start, std_start * 60 * 60)
    # back to datetime (ignore sub-minute resolution)
    start = datetime.datetime.fromtimestamp(start).replace(second=0, microsecond=0)
    # clamp start
    min_start = datetime_from_string(min_start)
    max_start = datetime_from_string(max_start)
    start = min(max(start, min_start), max_start)

    # get trip duration
    duration = random.gauss(avg_driving, std_driving)
    duration = min(max(duration, min_driving), max_driving)
    stop = start + datetime.timedelta(hours=duration)
    stop = stop.replace(second=0, microsecond=0)

    # get trip distance
    distance = random.gauss(avg_distance, std_distance)
    distance = min(max(distance, min_distance), max_distance)

    return start.time(), stop.time(), distance


def generate(args, parser):
    """Generates a scenario JSON from input Parameters

    :param args: input arguments
    :type args: argparse.Namespace

    :return: None
    """
    if args.output is None:
        raise SystemExit("The following argument is required: output")

    random.seed(args.seed)

    # SIMULATION TIME
    try:
        start = datetime_from_isoformat(args.start_time)
    except ValueError:
        # start time could not be parsed. Use default value.
        default_start_time = parser.parse_args([]).start_time
        start = datetime_from_isoformat(default_start_time)
        warnings.warn("Start time could not be parsed. Use ISO format like YYYY-MM-DDTHH:MM."
                      f"Default start time {default_start_time} will be used.")
    start = start.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=2)))
    stop = start + datetime.timedelta(days=args.days)
    interval = datetime.timedelta(minutes=args.interval)

    # VEHICLES
    if args.cars is None:
        args.cars = [['1', 'golf'], ['1', 'sprinter']]

    if args.vehicle_types is None:
        args.vehicle_types = "examples/vehicle_types.json"
        print("No definition of vehicle types found, using {}".format(args.vehicle_types))
    ext = args.vehicle_types.split('.')[-1]
    if ext != "json":
        print("File extension mismatch: vehicle type file should be .json")
    with open(args.vehicle_types) as f:
        vehicle_types = json.load(f)

    for count, vehicle_type in args.cars:
        assert vehicle_type in vehicle_types,\
            'The given vehicle type "{}" is not valid. Should be one of {}'\
            .format(vehicle_type, list(vehicle_types.keys()))
        vehicle_types[vehicle_type]["count"] = int(count)

    # VEHICLES WITH THEIR CHARGING STATION
    vehicles = {}
    batteries = {}
    charging_stations = {}
    for name, t in vehicle_types.items():
        for i in range(t.get("count", 0)):
            v_name = "{}_{}".format(name, i)
            cs_name = "CS_" + v_name
            vehicles[v_name] = {
                "connected_charging_station": cs_name,
                "estimated_time_of_departure": None,
                "desired_soc": None,
                "soc": args.min_soc,
                "vehicle_type": name
            }

            cs_power = max([v[1] for v in t['charging_curve']])
            charging_stations[cs_name] = {
                "max_power": cs_power,
                "min_power": 0,  # Could be set to "0.1 * cs_power"
                "parent": "GC1"
            }

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

    events = {
        "grid_operator_signals": [],
        "external_load": {},
        "energy_feed_in": {},
        "vehicle_events": []
    }

    # save path and options for CSV timeseries
    # all paths are relative to output file
    target_path = path.dirname(args.output)

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
            print("Warning: external csv file '{}' does not exist yet".format(ext_csv_path))

    if args.include_feed_in_csv:
        filename = args.include_feed_in_csv
        basename = path.splitext(path.basename(filename))[0]
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
        feed_in_path = path.join(target_path, filename)
        if not path.exists(feed_in_path):
            print("Warning: feed-in csv file '{}' does not exist yet".format(feed_in_path))

    if args.include_price_csv:
        filename = args.include_price_csv
        # basename = path.splitext(path.basename(filename))[0]
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
        price_csv_path = path.join(target_path, filename)
        if not path.exists(price_csv_path):
            print("Warning: price csv file '{}' does not exist yet".format(price_csv_path))

    daily = datetime.timedelta(days=1)

    # count number of trips where desired_soc is above min_soc
    trips_above_min_soc = 0

    # create vehicle and price events
    # each day (except Sunday), each vehicle leaves and returns after using some battery power

    now = start - daily
    while now < stop + 2*daily:
        now += daily

        # create vehicle events for this day
        for v_id, v in vehicles.items():
            if now.weekday() == 6:
                # no driving on Sunday
                break

            # get vehicle infos
            capacity = vehicle_types[v["vehicle_type"]]["capacity"]
            # convert mileage per 100 km in 1 km
            mileage = vehicle_types[v["vehicle_type"]]["mileage"] / 100

            # generate trip event
            dep_time, arr_time, distance = generate_trip(args)
            departure = datetime.datetime.combine(now.date(), dep_time, now.tzinfo)
            arrival = datetime.datetime.combine(now.date(), arr_time, now.tzinfo)
            soc_delta = distance * mileage / capacity

            desired_soc = soc_delta * (1 + vars(args).get("buffer", 0.1))
            desired_soc = max(args.min_soc, desired_soc)
            # update initial desired SoC
            v["desired_soc"] = v["desired_soc"] or desired_soc
            update = {
                "estimated_time_of_departure": departure.isoformat(),
                "desired_soc": desired_soc
            }

            if "last_arrival_idx" in v:
                # update last arrival event
                events["vehicle_events"][v["last_arrival_idx"]]["update"].update(update)
            else:
                # first event for this car: update directly
                v.update(update)

            if now >= stop:
                # after end of scenario: keep generating trips, but don't include in scenario
                continue

            trips_above_min_soc += desired_soc > args.min_soc

            events["vehicle_events"].append({
                "signal_time": departure.isoformat(),
                "start_time": departure.isoformat(),
                "vehicle_id": v_id,
                "event_type": "departure",
                "update": {
                    "estimated_time_of_arrival": arrival.isoformat()
                }
            })

            v["last_arrival_idx"] = len(events["vehicle_events"])

            events["vehicle_events"].append({
                "signal_time": arrival.isoformat(),
                "start_time": arrival.isoformat(),
                "vehicle_id": v_id,
                "event_type": "arrival",
                "update": {
                    "connected_charging_station": "CS_" + v_id,
                    "estimated_time_of_departure": None,
                    "desired_soc": None,
                    "soc_delta": -soc_delta
                }
            })

        # generate prices for the day
        if not args.include_price_csv and now < stop:
            morning = now + datetime.timedelta(hours=6)
            evening_by_month = now + datetime.timedelta(hours=22-abs(6-now.month))
            events['grid_operator_signals'] += [{
                # day (6-evening): 15ct
                "signal_time": max(start, now-daily).isoformat(),
                "grid_connector_id": "GC1",
                "start_time": morning.isoformat(),
                "cost": {
                    "type": "fixed",
                    "value": 0.15 + random.gauss(0, 0.05)
                }
            }, {
                # night (depending on month - 6): 5ct
                "signal_time": max(start, now-daily).isoformat(),
                "grid_connector_id": "GC1",
                "start_time": evening_by_month.isoformat(),
                "cost": {
                    "type": "fixed",
                    "value": 0.05 + random.gauss(0, 0.03)
                }
            }]

    # end of scenario

    # remove temporary information, only retain vehicle types that are actually present
    vehicle_types_present = {}
    for k, v in vehicle_types.items():
        if "count" in v and v["count"] > 0:
            del v["count"]
            vehicle_types_present[k] = v
    for v in vehicles.values():
        del v["last_arrival_idx"]

    j = {
        "scenario": {
            "start_time": start.isoformat(),
            # "stop_time": stop.isoformat(),
            "interval": interval.days * 24 * 60 + interval.seconds // 60,
            "n_intervals": (stop - start) // interval,
            "discharge_limit": args.discharge_limit
        },
        "constants": {
            "vehicle_types": vehicle_types_present,
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

    if trips_above_min_soc:
        print("{} trips use more than {}% capacity".format(trips_above_min_soc, args.min_soc * 100))

    # Write JSON
    with open(args.output, 'w') as f:
        json.dump(j, f, indent=2)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Generate scenarios as JSON files for vehicle charging modelling')
    parser.add_argument('output', nargs='?', help='output file name (example.json)')
    parser.add_argument('--cars', metavar=('N', 'TYPE'), nargs=2, action='append', type=str,
                        help='set number of cars for a vehicle type, \
                        e.g. `--cars 100 sprinter` or `--cars 13 golf`')
    parser.add_argument('--days', metavar='N', type=int, default=30,
                        help='set duration of scenario as number of days')
    parser.add_argument('--interval', metavar='MIN', type=int, default=15,
                        help='set number of minutes for each timestep (Δt)')
    parser.add_argument('--start-time', default='2018-01-01T00:15:00+00:00',
                        help='Provide start time of simulation in ISO format YYYY-MM-DDTHH:MM:SS. '
                             'Precision is 1 minute. E.g. 2018-01-31T00:15:00+00:00')
    parser.add_argument('--min-soc', metavar='SOC', type=float, default=0.8,
                        help='set minimum desired SOC (0 - 1) for each charging process')
    parser.add_argument('--battery', '-b', default=[], nargs=2, type=float, action='append',
                        help='add battery with specified capacity in kWh and C-rate \
                        (-1 for variable capacity, second argument is fixed power))')
    parser.add_argument('--gc-power', type=int, default=530, help='set power at grid connection '
                                                                  'point in kW')
    parser.add_argument('--seed', default=None, type=int, help='set random seed')

    parser.add_argument('--vehicle-types', default=None,
                        help='location of vehicle type definitions')
    parser.add_argument('--discharge_limit', default=0.5,
                        help='Minimum SoC to discharge to during v2g. [0-1]')
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
    parser.add_argument('--config', help='Use config file to set arguments')

    args = parser.parse_args()

    set_options_from_config(args, check=False, verbose=False)

    generate(args, parser)
