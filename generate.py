#!/usr/bin/env python3

import argparse
import datetime
import json
import random
from os import path

from src.util import datetime_from_isoformat, set_options_from_config


def generate(args):
    """Generates a scenario JSON from input Parameters
    args: argparse.Namespace
    """
    if args.output is None:
        raise SystemExit("The following argument is required: output")

    if not args.cars:
        args.cars = [['2', 'golf'], ['3', 'sprinter']]

    start = datetime.datetime(year=2020, month=1, day=1,
                              tzinfo=datetime.timezone(datetime.timedelta(hours=2)))
    stop = start + datetime.timedelta(days=args.days)
    interval = datetime.timedelta(minutes=args.interval)

    # CONSTANTS
    avg_distance = vars(args).get("avg_distance", 40)  # km
    std_distance = vars(args).get("std_distance", 2.155)

    # VEHICLE TYPES
    vehicle_types = {
        "sprinter": {
            "name": "sprinter",
            "capacity": 70,  # kWh
            "mileage": 40,  # kWh / 100km
            "charging_curve": [[0, 11], [80, 11], [100, 0]],  # SOC -> kWh
            "min_charging_power": 0,
            "count": 0
        },
        "golf": {
            "name": "E-Golf",
            "capacity": 50,
            "mileage": 16,
            "charging_curve": [[0, 22], [80, 22], [100, 0]],
            "min_charging_power": 0,
            "count": 0
        }
    }

    for count, vehicle_type in args.cars:
        assert vehicle_type in vehicle_types,\
            'The given vehicle type "{}" is not valid. Should be one of {}'\
            .format(vehicle_type, list(vehicle_types.keys()))

        count = int(count)
        vehicle_types[vehicle_type]['count'] = count

    # VEHICLES WITH THEIR CHARGING STATION
    vehicles = {}
    batteries = {}
    charging_stations = {}
    for name, t in vehicle_types.items():
        for i in range(t["count"]):
            v_name = "{}_{}".format(name, i)
            cs_name = "CS_" + v_name
            is_connected = True
            depart = start + datetime.timedelta(days=1, hours=6, minutes=15 * random.randint(0, 4))
            soc = random.randint(50, 100)
            vehicles[v_name] = {
                "connected_charging_station": cs_name,
                "estimated_time_of_departure": depart.isoformat(),
                "desired_soc": args.min_soc,
                "soc": soc,
                "vehicle_type": name
            }

            charging_stations[cs_name] = {
                "max_power": max([v[1] for v in t['charging_curve']]),
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
            "charging_curve": [[0, max_power], [100, max_power]]
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
        basename = filename.split('.')[0]
        options = {
            "csv_file": filename,
            "start_time": start.isoformat(),
            "step_duration_s": 900,  # 15 minutes
            "grid_connector_id": "GC1",
            "column": "energy"
        }
        if args.include_ext_csv_option:
            for key, value in args.include_ext_csv_option:
                options[key] = value
        events['external_load'][basename] = options
        # check if CSV file exists
        ext_csv_path = path.join(target_path, filename)
        if not path.exists(ext_csv_path):
            print("Warning: external csv file '{}' does not exist yet".format(ext_csv_path))

    if args.include_feed_in_csv:
        filename = args.include_feed_in_csv
        basename = filename.split('.')[0]
        options = {
            "csv_file": filename,
            "start_time": start.isoformat(),
            "step_duration_s": 3600,  # 60 minutes
            "grid_connector_id": "GC1",
            "column": "energy"
        }
        if args.include_feed_in_csv_option:
            for key, value in args.include_feed_in_csv_option:
                options[key] = value
        events['energy_feed_in'][basename] = options
        feed_in_path = path.join(target_path, filename)
        if not path.exists(feed_in_path):
            print("Warning: feed-in csv file '{}' does not exist yet".format(feed_in_path))

    if args.include_price_csv:
        filename = args.include_price_csv
        basename = filename.split('.')[0]
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
        price_csv_path = path.join(target_path, filename)
        if not path.exists(price_csv_path):
            print("Warning: price csv file '{}' does not exist yet".format(price_csv_path))
    else:
        events['grid_operator_signals'].append({
            "signal_time": start.isoformat(),
            "grid_connector_id": "GC1",
            "start_time": start.isoformat(),
            "cost": {
                "type": "polynomial",
                "value": [0.0, 0.1, 0.0]
            }
        })

    daily = datetime.timedelta(days=1)
    hourly = datetime.timedelta(hours=1)

    # count number of trips where desired_soc is above min_soc
    trips_above_min_soc = 0

    # create vehicle events
    # each day, each vehicle leaves between 6 and 7 and returns after using some battery power

    now = start
    while now < stop:
        # next day. First day is off
        now += daily

        if not args.include_price_csv:
            evening_by_month = datetime.timedelta(days=1, hours=22-abs(6-now.month))
            # generate grid op signal for next day
            events['grid_operator_signals'] += [{
                # day (6-evening): 15ct
                "signal_time": now.isoformat(),
                "grid_connector_id": "GC1",
                "start_time": (now + datetime.timedelta(days=1, hours=6)).isoformat(),
                "cost": {
                    "type": "fixed",
                    "value": 0.15 + random.gauss(0, 0.05)
                }
            }, {
                # night (depending on month - 6): 5ct
                "signal_time": now.isoformat(),
                "grid_connector_id": "GC1",
                "start_time": (now + evening_by_month).isoformat(),
                "cost": {
                    "type": "fixed",
                    "value": 0.05 + random.gauss(0, 0.03)
                }
            }]

        for v_id, v in vehicles.items():
            if now.weekday() == 6:
                # no work on Sunday
                break

            capacity = vehicle_types[v["vehicle_type"]]["capacity"]
            mileage = vehicle_types[v["vehicle_type"]]["mileage"]

            # get distance for the day (computed before)
            distance = v.get("distance", random.gauss(avg_distance, std_distance))
            soc_delta = distance * mileage / capacity

            # departure
            dep_str = v.get('departure', v["estimated_time_of_departure"])
            dep_time = datetime_from_isoformat(dep_str)
            # now + datetime.timedelta(hours=6, minutes=15 * random.randint(0,4))
            # always 8h
            t_delta = datetime.timedelta(hours=8)
            # 40 km -> 6h
            # l = log(1 - 6/8) / 40
            # t_delta = datetime.timedelta(hours=8 * (1 - exp(l * distance)))
            # t_delta = t_delta - datetime.timedelta(microseconds=t_delta.microseconds)
            arrival_time = dep_time + t_delta

            events["vehicle_events"].append({
                "signal_time": now.isoformat(),
                "start_time": dep_time.isoformat(),
                "vehicle_id": v_id,
                "event_type": "departure",
                "update": {
                    "estimated_time_of_arrival": arrival_time.isoformat()
                }
            })

            # plan next day
            if now.weekday() == 5:
                # today is Saturday, tomorrow is Sunday: no work
                next_dep_time = now + \
                    datetime.timedelta(days=2, hours=6, minutes=15 * random.randint(0, 4))
            else:
                next_dep_time = now + \
                    datetime.timedelta(days=1, hours=6, minutes=15 * random.randint(0, 4))

            next_distance = random.gauss(avg_distance, std_distance)
            next_distance = min(max(17, next_distance), 120)
            soc_needed = next_distance * mileage / capacity
            v['distance'] = next_distance
            v["departure"] = next_dep_time.isoformat()

            desired_soc = soc_needed * (1 + vars(args).get("buffer", 0.1))
            trips_above_min_soc += desired_soc > args.min_soc
            desired_soc = max(args.min_soc, desired_soc)

            events["vehicle_events"].append({
                "signal_time": arrival_time.isoformat(),
                "start_time": arrival_time.isoformat(),
                "vehicle_id": v_id,
                "event_type": "arrival",
                "update": {
                    "connected_charging_station": "CS_" + v_id,
                    "estimated_time_of_departure": next_dep_time.isoformat(),
                    "desired_soc": desired_soc,
                    "soc_delta": -soc_delta
                }
            })

    # reset initial SOC
    for v in vehicles.values():
        del v["distance"]
        del v["departure"]

    j = {
        "scenario": {
            "start_time": start.isoformat(),
            "interval": int(interval.days * 24 * 60 + interval.seconds/60),
            "n_intervals": int((stop - start) / interval)
        },
        "constants": {
            "vehicle_types": vehicle_types,
            "vehicles": vehicles,
            "grid_connectors": {
                "GC1": {
                    "max_power": 630
                }
            },
            "charging_stations": charging_stations,
            "batteries": batteries
        },
        "events": events
    }

    if trips_above_min_soc:
        print("{} trips use more than {}% capacity".format(trips_above_min_soc, args.min_soc))

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
    parser.add_argument('--min-soc', metavar='SOC', type=int, default=80,
                        help='set minimum desired SOC (0%% - 100%%) for each charging process')
    parser.add_argument('--battery', '-b', default=[], nargs=2, type=float, action='append',
                        help='add battery with specified capacity in kWh and C-rate \
                        (-1 for variable capacity, second argument is fixed power))')

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

    generate(args)
