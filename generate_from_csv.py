#!/usr/bin/env python3

import argparse
import csv
import datetime
import json
from os import path
import random
import warnings
import bisect

from src.util import set_options_from_config

DATETIME_FORMAT = '%Y-%m-%d %H:%M:%S'


def generate_from_csv(args):
    """Generates a scenario JSON from csv rotation schedule of fleets to/from one grid connector.

    note: only one grid connector supported. Each line in the csv represents one trip. Each
    vehicle_id represents one vehicle. If the column vehicle_id is not given, the trips are assigned
    to the vehicles by the principle: first in, first out. Note that in this case a minimum standing
    time can be assigned to control the minimum time a vehicle can charge at the depot.

    Needed columns:
    - departure time in YYYY-MM-DD HH:MM:SS
    - arrival time in YYYY-MM-DD HH:MM:SS
    - vehicle_type (as in examples/vehicle_types.json)
    - soc (SoC at arrival) or delta_soc in [0,1] (optional, if not given the mileage is taken
    instead)
    - vehicle_id (optinal, see explanation above)
    - distance in km (optinal, needed if columns soc or delta_soc are not given)


    :param args: input arguments
    :type args: argparse.Namespace
    :return: None
    """
    missing = [arg for arg in ["input_file", "output"] if vars(args).get(arg) is None]
    if missing:
        raise SystemExit("The following arguments are required: {}".format(", ".join(missing)))

    random.seed(args.seed)

    interval = datetime.timedelta(minutes=args.interval)
    # read csv input file
    input = csv_to_dict(args.input_file)
    # define target path for relative input or output files
    target_path = path.dirname(args.output)

    # VEHICLES
    if args.vehicle_types is None:
        args.vehicle_types = "examples/vehicle_types.json"
        print("No definition of vehicle types found, using {}".format(args.vehicle_types))
    ext = args.vehicle_types.split('.')[-1]
    if ext != "json":
        print("File extension mismatch: vehicle type file should be .json")
    with open(args.vehicle_types) as f:
        predefined_vehicle_types = json.load(f)

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
    for vehicle_type in {item['vehicle_type'] for item in input}:
        # update vehicle types with vehicles in input csv
        try:
            vehicle_types.update({vehicle_type: predefined_vehicle_types[vehicle_type]})
        except KeyError:
            print(f"The vehicle type {vehicle_type} defined in the input csv cannot be found in "
                  f"vehicle_types.json. Please check for consistency.")

    if "vehicle_id" not in input[0].keys():
        warnings.warn("Column 'vehicle_id' missing, vehicles are assigned by the principle first in"
                      ", first out.")
        if args.export_vehicle_id_csv != "None" and args.export_vehicle_id_csv is not None:
            export_filename = path.join(target_path, args.export_vehicle_id_csv)
        else:
            export_filename = None
        recharge_fraction = vars(args).get("recharge_fraction", 1)
        input = assign_vehicle_id(input, vehicle_types, recharge_fraction, export_filename)

    if "connect_cs" not in input[0].keys():
        warnings.warn("Column 'connect_cs' is not available. Vehicles will be connected to a "
                      "charging station after every trip.")
        input = [dict(item, **{'connect_cs': 1}) for item in input]

    for vehicle_id in {item['vehicle_id'] for item in input}:
        vt = [d for d in input if d['vehicle_id'] == vehicle_id][0]["vehicle_type"]
        v_name = vehicle_id
        cs_name = "CS_" + v_name
        # define start conditions
        vehicles[v_name] = {
            "connected_charging_station": None,
            "estimated_time_of_departure": None,
            "desired_soc": args.min_soc,
            "soc": args.min_soc,
            "vehicle_type": vt
        }

        cs_power = max([v[1] for v in vehicle_types[vt]['charging_curve']])
        charging_stations[cs_name] = {
            "max_power": cs_power,
            "min_power": vars(args).get("cs_power_min", 0),
            "parent": "GC1"
        }

        # filter all rides for that vehicle
        vid_list = []
        [vid_list.append(row) for row in input if (row["vehicle_id"] == v_name)]

        # sort events for their departure time, so that the matching departure time of an
        # arrival event can be read out of the next element in vid_list
        vid_list = sorted(vid_list, key=lambda x: x["departure_time"])
        for index, row in enumerate(vid_list):
            departure_event_in_input = True
            arrival = row["arrival_time"]
            arrival = datetime.datetime.strptime(arrival, DATETIME_FORMAT)
            try:
                departure = vid_list[index+1]["departure_time"]
                departure = datetime.datetime.strptime(departure, DATETIME_FORMAT)
                next_arrival = vid_list[index+1]["arrival_time"]
                next_arrival = datetime.datetime.strptime(next_arrival, DATETIME_FORMAT)
            except IndexError:
                departure_event_in_input = False
                departure = arrival + datetime.timedelta(hours=8)

            # check if column delta_soc or column soc exists
            if "delta_soc" not in row.keys():
                if "soc" in row.keys():
                    delta_soc = 1 - float(row["soc"])
                else:
                    # get vehicle infos
                    capacity = vehicle_types[vt]["capacity"]
                    try:
                        # convert mileage per 100 km in 1 km
                        mileage = vehicle_types[vt]["mileage"] / 100
                    except ValueError:
                        print("In order to assign the vehicle consumption, either a mileage must"
                              "be given in vehicle_types.json or a soc or delta_soc must be "
                              "given in the input file. Please check for consistency.")
                    try:
                        distance = float(row["distance"])
                    except ValueError:
                        print("In order to assign the vehicle consumption via the mileage, the "
                              "column 'distance' must be given in the input csv. Please check "
                              "for consistency.")
                    delta_soc = distance * mileage / capacity
            else:
                delta_soc = float(row["delta_soc"])
            if int(row["connect_cs"]) == 1:
                connect_cs = "CS_" + v_name
            else:
                connect_cs = None

            events["vehicle_events"].append({
                "signal_time": arrival.isoformat(),
                "start_time": arrival.isoformat(),
                "vehicle_id": v_name,
                "event_type": "arrival",
                "update": {
                    "connected_charging_station": connect_cs,
                    "estimated_time_of_departure": departure.isoformat(),
                    "soc_delta": -delta_soc,
                }
            })

            # give warning if desired_soc < soc_delta
            if args.min_soc < delta_soc:
                print(f"The minimum desired soc of {args.min_soc} is lower than the delta_soc"
                      f" of the next ride.")

            if departure_event_in_input:
                events["vehicle_events"].append({
                    "signal_time": departure.isoformat(),
                    "start_time": departure.isoformat(),
                    "vehicle_id": v_name,
                    "event_type": "departure",
                    "update": {
                        "estimated_time_of_arrival":  next_arrival.isoformat(),
                    }
                })

    # add stationary battery
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
    times = []
    for row in input:
        times.append(row["departure_time"])
    times.sort()
    start = times[0]
    start = datetime.datetime.strptime(start, DATETIME_FORMAT)
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
    if not args.include_price_csv:
        now = start - daily
        while now < stop + 2 * daily:
            now += daily
            for v_id, v in vehicles.items():
                if now >= stop:
                    # after end of scenario: keep generating trips, but don't include in scenario
                    continue

            # generate prices for the day
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


def csv_to_dict(csv_path):
    """
    Reads csv file and returns a dict with each element representing a trip

    :param csv_path: path to input csv file
    :type csv_path: str
    :return: dictionary
    :rtype: dict
    """

    dict = []
    with open(csv_path, 'r') as file:
        reader = csv.reader(file)
        # set column names using first row
        columns = next(reader)

        # convert csv to json
        for row in reader:
            row_data = {}
            for i in range(len(row)):
                # set key names
                row_key = columns[i].lower()
                # set key/value
                row_data[row_key] = row[i]
            # add data to json store
            dict.append(row_data)
    return dict


def assign_vehicle_id(input, vehicle_types, recharge_fraction, export=None):
    """
    Assigns all rotations to specific vehicles with distinct vehicle_id. The assignment follows the
    principle "first in, first out". The assignment of a minimum standing time in hours is optional.

    :param input: schedule of rotations
    :type input: dict
    :param vehicle_types: dict with vehicle types
    :type vehicle_types: dict
    :param recharge_fraction: minimum fraction of capacity for recharge when leaving the charging
                              station
    :type recharge_fraction: float
    :param export: path to output file of input with vehicle_id
    :type export: str or None
    :return: schedule of rotations
    :rtype: dict
    """
    rotations_in_progress = []
    idle_vehicles = []
    vehicle_type_counts = {vehicle_type: 0 for vehicle_type in vehicle_types.keys()}

    rotations = sorted(input, key=lambda d: d.get('departure_time'))

    for rot in rotations:
        # find vehicles that have completed rotation and stood for a minimum standing time
        # mark those vehicle as idle
        for r in rotations_in_progress:
            # calculate min_standing_time at a charging station for each vehicle
            capacity = vehicle_types[r["vehicle_type"]]["capacity"]
            cs_power = max([v[1] for v in vehicle_types[r["vehicle_type"]]['charging_curve']])
            min_standing_time = (capacity / cs_power) * recharge_fraction
            min_standing_time = datetime.timedelta(hours=min_standing_time)
            departure_time = datetime.datetime.strptime(rot["departure_time"], DATETIME_FORMAT)
            arrival_time = datetime.datetime.strptime(r["arrival_time"], DATETIME_FORMAT)

            if departure_time - arrival_time > min_standing_time:
                idle_vehicles.append(r["vehicle_id"])
                rotations_in_progress.pop(0)
            else:
                break

        # find idle vehicle for rotation if exists
        # else generate new vehicle id
        vt = rot["vehicle_type"]
        try:
            # find idle vehicle for rotation
            id = next(id for id in idle_vehicles if vt in id)
            idle_vehicles.remove(id)
        except StopIteration:
            # no vehicle idle: generate new vehicle id
            vehicle_type_counts[vt] += 1
            id = f"{vt}_{vehicle_type_counts[vt]}"

        rot["vehicle_id"] = id
        arrival_times = [r["arrival_time"] for r in rotations_in_progress]
        # keep list of ongoing rotations sorted by arrival_time
        rotations_in_progress.insert(bisect.bisect(arrival_times, rot["arrival_time"]), rot)
    if export:
        all_rotations = []
        header = []
        for rotation_id, rotation in enumerate(input):
            if not header:
                for k, v in rotation.items():
                    header.append(k)
            all_rotations.append(rotation)

        with open(export, 'w', encoding='UTF8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=header)
            writer.writeheader()
            writer.writerows(all_rotations)

    return input


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Generate scenarios as JSON files for vehicle charging modelling')
    parser.add_argument('input_file', nargs='?',
                        help='input file name (rotations_example_table.csv)')
    parser.add_argument('output', nargs='?', help='output file name (example.json)')
    parser.add_argument('--days', metavar='N', type=int, default=30,
                        help='set duration of scenario as number of days')
    parser.add_argument('--interval', metavar='MIN', type=int, default=15,
                        help='set number of minutes for each timestep (Δt)')
    parser.add_argument('--min-soc', metavar='SOC', type=float, default=0.8,
                        help='set minimum desired SOC (0 - 1) for each charging process')
    parser.add_argument('--battery', '-b', default=[], nargs=2, type=float, action='append',
                        help='add battery with specified capacity in kWh and C-rate \
                        (-1 for variable capacity, second argument is fixed power))')
    parser.add_argument('--gc-power', type=float, default=530, help='set power at grid connection '
                                                                    'point in kW')
    parser.add_argument('--cs-power-min', type=float, default=0, help='set minimal power at '
                                                                      'charging station in kW')
    parser.add_argument('--seed', default=None, type=int, help='set random seed')
    parser.add_argument('--recharge-fraction', type=float, default=1,
                        help='Minimum fraction of vehicle battery capacity for recharge when '
                             'leaving the charging station')

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
    parser.add_argument('--export-vehicle-id-csv', default=None,
                        help='option to export csv after assigning vehicle_id')
    parser.add_argument('--config', help='Use config file to set arguments')

    args = parser.parse_args()

    set_options_from_config(args, check=False, verbose=False)

    generate_from_csv(args)
