#!/usr/bin/env python3

import argparse
import datetime
import json
import random
import warnings
from os import path

from src.util import set_options_from_config, datetime_from_isoformat


DEFAULT_START_TIME = "2023-01-01T01:00:00+02:00"


def datetime_from_string(s):
    h, m = map(int, s.split(':'))
    return datetime.datetime(1972, 1, 1, h, m)


def generate_trip(v_type_info):
    """
    Creates randomly generated trips from average input arguments

    :param v_type_info: info of used vehicle_type
    :type v_type_info: dict
    :raises Exception: if the time format is not hh:mm
    :return: start (datetime), duration (timedelta), distance (float)
    """

    stat_values = v_type_info["statistical_values"]

    # create trip dictionary with statistical values for current vehicle
    trip = {"avg_distance": stat_values["distance_in_km"].get("avg_distance"),
            "std_distance": stat_values["distance_in_km"].get("std_distance"),
            "min_distance": stat_values["distance_in_km"].get("min_distance"),
            "max_distance": stat_values["distance_in_km"].get("max_distance"),
            "avg_start": stat_values["departure"].get("avg_start"),
            "std_start": stat_values["departure"].get("std_start_in_hours"),
            "min_start": stat_values["departure"].get("min_start"),
            "max_start": stat_values["departure"].get("max_start"),
            "avg_driving": stat_values["duration_in_hours"].get("avg_driving"),
            "std_driving": stat_values["duration_in_hours"].get("std_driving"),
            "min_driving": stat_values["duration_in_hours"].get("min_driving"),
            "max_driving": stat_values["duration_in_hours"].get("max_driving")}

    # check for missing or invalid parameters in statistical values file
    for k, v in trip.items():
        # check all necessary info is given
        assert v is not None, (f"Parameter '{k}' missing for vehicle type "
                               f"'{v_type_info['name']}'. "
                               "Please provide statistical values in vehicle_type.json.")
        # parse times from string
        if k in ["avg_start", "min_start", "max_start"]:
            try:
                trip[k] = datetime_from_string(v)
            except Exception:
                print(f"Format of '{k}' is invalid. Please provide the time in format 'hh:mm'.")
                raise
        else:
            # make sure non-time arguments are numbers
            assert type(v) in [int, float], f"'{k}' must be given as integer or float."

    # start time
    start = trip["avg_start"]
    # to timestamp (resolution in seconds)
    start = start.timestamp()
    # apply normal distribution (hours -> seconds)
    start = random.gauss(start, trip["std_start"] * 60 * 60)
    # back to datetime (ignore sub-minute resolution)
    start = datetime.datetime.fromtimestamp(start).replace(second=0, microsecond=0)
    # clamp start
    min_start = trip["min_start"]
    max_start = trip["max_start"]
    start = min(max(start, min_start), max_start)

    # get trip duration
    # random distribution
    duration = random.gauss(trip["avg_driving"], trip["std_driving"])
    # clipping to min/max
    duration = min(max(duration, trip["min_driving"]), trip["max_driving"])
    duration = datetime.timedelta(hours=duration)
    # ignore sub-minute resolution
    duration = datetime.timedelta(minutes=duration // datetime.timedelta(minutes=1))
    # get trip distance
    distance = random.gauss(trip["avg_distance"], trip["std_distance"])
    distance = min(max(distance, trip["min_distance"]), trip["max_distance"])

    return start.time(), duration, distance


def generate(args):
    """Generates a scenario JSON from input Parameters

    :param args: input arguments
    :type args: argparse.Namespace
    :raises SystemExit: if the required argument *output* is missing
    """

    # check for necessary argument: output
    if args.output is None:
        raise SystemExit("The following argument is required: output")

    # argument 'min_soc_threshold' has no relevance for generation of synthetic driving profiles
    soc_threshold = vars(args).get("min_soc_threshold")
    if soc_threshold:
        warnings.warn("Argument 'min_soc_threshold' has no relevance for generation "
                      "of driving profiles.")

    # set seed
    random.seed(args.seed)

    # SIMULATION TIME
    try:
        start = datetime_from_isoformat(args.start_time)
    except ValueError:
        # start time could not be parsed. Use default value.
        start = datetime_from_isoformat(DEFAULT_START_TIME)
        warnings.warn("Start time could not be parsed. "
                      "Use ISO format like YYYY-MM-DDTHH:MM:SS+TZ:TZ. "
                      f"Default start time {DEFAULT_START_TIME} will be used.")
    start = start.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=2)))
    stop = start + datetime.timedelta(days=args.days)
    # define interval for simulation
    interval = datetime.timedelta(minutes=args.interval)
    # define target path for relative output files
    target_path = path.dirname(args.output)

    # use default vehicles, if args.vehicles does not exist
    if args.vehicles is None:
        args.vehicles = [['1', 'golf'], ['1', 'sprinter']]

    # get defined vehicle types
    if args.vehicle_types is None:
        args.vehicle_types = "examples/vehicle_types.json"
        print(f"No definition of vehicle types found, using {args.vehicle_types}")
    ext = args.vehicle_types.split('.')[-1]
    if ext != "json":
        warnings.warn("File extension mismatch: vehicle type file should be '.json'")
    with open(args.vehicle_types) as f:
        predefined_vehicle_types = json.load(f)

    # INITIALIZE CONSTANTS AND EVENTS
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

    # count number of trips for which desired_soc is above min_soc
    trips_above_min_soc = 0
    trips_total = 0

    # update vehicle types with vehicle types actually present
    for count, v_type in args.vehicles:
        assert v_type in predefined_vehicle_types, \
            f"The given vehicle type '{v_type}' is not valid. " \
            f"Should be one of {list(predefined_vehicle_types.keys())}."
        vehicle_types.update({v_type: predefined_vehicle_types[v_type]})
        vehicle_types[v_type]["count"] = int(count)

    for v_type, v_type_info in vehicle_types.items():
        for i in range(v_type_info.get("count", 0)):
            v_id = "{}_{}".format(v_type, i)
            cs_id = "CS_" + v_id
            vehicles[v_id] = {
                "connected_charging_station": cs_id,
                "estimated_time_of_departure": None,
                "desired_soc": None,
                "soc": args.min_soc,
                "vehicle_type": v_type
            }

            cs_power = max([v[1] for v in v_type_info['charging_curve']])
            charging_stations[cs_id] = {
                "max_power": cs_power,
                "min_power": args.cs_power_min if args.cs_power_min else 0.1 * cs_power,
                "parent": "GC1"
            }

    # GENERATE VEHICLE EVENTS: daily
    daily = datetime.timedelta(days=1)
    now = start - daily
    while now < stop + 2 * daily:
        now += daily

        # create vehicle events for this day
        for v_id, v_info in vehicles.items():
            # check if day is defined as a no driving day for this vehicle_type
            if now.weekday() in vehicle_types[v_info["vehicle_type"]].get("no_drive_days", []):
                continue
            if now.date().isoformat() in vars(args).get("holidays", []):
                break

            # get vehicle infos
            capacity = vehicle_types[v_info["vehicle_type"]]["capacity"]
            # convert mileage per 100 km in 1 km
            mileage = vehicle_types[v_info["vehicle_type"]]["mileage"] / 100

            # generate trip event
            dep_time, duration, distance = generate_trip(vehicle_types[v_info["vehicle_type"]])
            departure = datetime.datetime.combine(now.date(), dep_time, now.tzinfo)
            arrival = departure + duration
            soc_delta = distance * mileage / capacity

            desired_soc = soc_delta * (1 + vars(args).get("buffer", 0.1))
            desired_soc = max(args.min_soc, desired_soc)
            # update initial desired SoC
            v_info["desired_soc"] = v_info["desired_soc"] or desired_soc
            update = {
                "estimated_time_of_departure": departure.isoformat(),
                "desired_soc": desired_soc
            }

            if "last_arrival_idx" in v_info:
                if v_info["arrival"] >= departure:
                    # still on last trip, discard new trip
                    continue
                # update last arrival event
                events["vehicle_events"][v_info["last_arrival_idx"]]["update"].update(update)
            else:
                # first event for this vehicle: update directly
                v_info.update(update)

            if now >= stop:
                # after end of scenario: keep generating trips, but don't include in scenario
                continue

            trips_above_min_soc += desired_soc > args.min_soc
            trips_total += 1

            events["vehicle_events"].append({
                "signal_time": departure.isoformat(),
                "start_time": departure.isoformat(),
                "vehicle_id": v_id,
                "event_type": "departure",
                "update": {
                    "estimated_time_of_arrival": arrival.isoformat()
                }
            })

            v_info["last_arrival_idx"] = len(events["vehicle_events"])
            v_info["arrival"] = arrival

            events["vehicle_events"].append({
                "signal_time": arrival.isoformat(),
                "start_time": arrival.isoformat(),
                "vehicle_id": v_id,
                "event_type": "arrival",
                "update": {
                    "connected_charging_station": "CS_" + v_id,
                    "estimated_time_of_departure": None,
                    "desired_soc": 0,
                    "soc_delta": -soc_delta
                }
            })
    # remove temporary information
    for v_info in vehicles.values():
        del v_info["last_arrival_idx"]
        del v_info["arrival"]

    # number of trips for which desired_soc is above min_soc
    if trips_above_min_soc:
        print(f"{trips_above_min_soc} of {trips_total} trips "
              f"use more than {args.min_soc * 100}% capacity")

    # add stationary battery
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

    # external load CSV
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
            warnings.warn(f"External csv file '{ext_csv_path}' does not exist yet.")

    # energy feed-in CSV (e.g. from PV)
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
            warnings.warn(f"Feed-in csv file '{feed_in_path}' does not exist yet.")

    # energy price CSV
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
            warnings.warn(f"Price csv file '{price_csv_path}' does not exist yet.")
    else:
        # generate prices for the day
        now = start - daily
        while now < stop + 2 * daily:
            now += daily

            if now < stop:
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

    # check voltage level (used in cost calculation)
    voltage_level = vars(args).get("voltage_level")
    if voltage_level is None:
        warnings.warn("Voltage level is not set. Please choose one when calculating costs.")

    # create final dict
    j = {
        "scenario": {
            "start_time": start.isoformat(),
            "interval": interval.days * 24 * 60 + interval.seconds // 60,
            "n_intervals": (stop - start) // interval,
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
        description='Generate scenarios as JSON files for vehicle charging modelling')
    parser.add_argument('output', nargs='?', help='output file name (example.json)')
    parser.add_argument('--vehicles', metavar=('N', 'TYPE'), nargs=2, action='append', type=str,
                        help='set number of vehicles for a vehicle type, \
                        e.g. `--vehicles 100 sprinter` or `--vehicles 13 golf`')
    parser.add_argument('--days', metavar='N', type=int, default=7,
                        help='set duration of scenario as number of days')
    parser.add_argument('--start-time', default=DEFAULT_START_TIME,
                        help='Provide start time of simulation in ISO format '
                             'YYYY-MM-DDTHH:MM:SS+TZ:TZ. Precision is 1 second. E.g. '
                             '2023-01-01T01:00:00+02:00')
    parser.add_argument('--holidays', default=None,
                        help='Provide list of specific days of no driving ISO format YYYY-MM-DD')

    # general
    parser.add_argument('--interval', metavar='MIN', type=int, default=15,
                        help='set number of minutes for each timestep (Δt)')
    parser.add_argument('--min-soc', metavar='SOC', type=float, default=0.8,
                        help='set minimum desired SOC (0 - 1) for each charging process')
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
                        help='Minimum SoC to discharge to during v2g. [0-1]')
    parser.add_argument('--seed', default=None, type=int, help='set random seed')

    # input files (CSV, JSON)
    parser.add_argument('--vehicle-types', default=None,
                        help='location of vehicle type definitions')
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

    # config
    parser.add_argument('--config', help='Use config file to set arguments')

    args = parser.parse_args()

    set_options_from_config(args, check=False, verbose=False)

    generate(args)
