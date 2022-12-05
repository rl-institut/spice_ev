#!/usr/bin/env python3

import argparse
import csv
import datetime
import json
from os import path
import random
import warnings

from src.util import set_options_from_config

DATETIME_FORMAT = '%Y-%m-%d %H:%M:%S'


def generate_from_csv(args):
    """Generates a scenario JSON from csv rotation schedule of fleets to/from one grid connector.

    note: only one grid connector supported. Each line in the csv represents one trip. Each
    vehicle_id represents one vehicle. If the column vehicle_id is not given, the trips are assigned
    to the vehicles by the principle: first in, first out. Note that in this case a minimum standing
    time can be assigned to control the minimum time a vehicle can charge at the depot.

    Needed columns:
    - departure_time in YYYY-MM-DD HH:MM:SS
    - arrival_time in YYYY-MM-DD HH:MM:SS
    - vehicle_type (as in examples/vehicle_types.json)
    - soc (SoC at arrival) or delta_soc in [0,1] (optional, if not given the mileage is taken
    instead)
    - vehicle_id (optional, see explanation above)
    - distance in km (optional, needed if columns soc or delta_soc are not given)


    :param args: input arguments
    :type args: argparse.Namespace
    :raises SystemExit: if any of the required arguments (*input_file* and *output*) is missing
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

    # get defined vehicle types
    if args.vehicle_types is None:
        args.vehicle_types = "examples/vehicle_types.json"
        print(f"No definition of vehicle types found, using {args.vehicle_types}.")
    ext = args.vehicle_types.split('.')[-1]
    if ext != "json":
        warnings.warn("File extension mismatch: vehicle type file should be '.json'.")
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
    for v_type in {item['vehicle_type'] for item in input}:
        try:
            vehicle_types.update({v_type: predefined_vehicle_types[v_type]})
        except KeyError:
            print(f"The vehicle type '{v_type}' defined in the input csv cannot be found in "
                  f"vehicle_types.json. Please check for consistency.")

    if "vehicle_id" not in input[0].keys():
        warnings.warn("Column 'vehicle_id' missing, vehicles are assigned by the principle first in"
                      ", first out.")
        if args.export_vehicle_id_csv != "None" and args.export_vehicle_id_csv is not None:
            export_filename = path.join(target_path, args.export_vehicle_id_csv)
        else:
            export_filename = None
        input = assign_vehicle_id(input, vehicle_types, export_filename)

    if "connect_cs" not in input[0].keys():
        warnings.warn("Column 'connect_cs' is not available. Vehicles will be connected to a "
                      "charging station after every trip.")
        input = [dict(item, **{'connect_cs': 1}) for item in input]

    for v_id in {item['vehicle_id'] for item in input}:
        v_type = [d for d in input if d['vehicle_id'] == v_id][0]["vehicle_type"]
        cs_id = "CS_" + v_id

        # define start conditions
        vehicles[v_id] = {
            "connected_charging_station": None,
            "estimated_time_of_departure": None,
            "soc": args.min_soc,
            "vehicle_type": v_type
        }

        cs_power = max([v[1] for v in vehicle_types[v_type]['charging_curve']])
        charging_stations[cs_id] = {
            "max_power": cs_power,
            "min_power": args.cs_power_min if args.cs_power_min else 0.1 * cs_power,
            "parent": "GC1"
        }

        # keep track of last arrival event to adjust desired SoC if needed
        last_arrival_event = None

        # filter all trips for that vehicle
        v_id_list = []
        [v_id_list.append(row) for row in input if (row["vehicle_id"] == v_id)]

        # sort events for their departure time, so that the matching departure time of an
        # arrival event can be read out of the next element in v_id_list
        v_id_list = sorted(v_id_list, key=lambda x: x["departure_time"])

        # initialize sum_delta_soc to add up delta_soc's of all trips until connected to a CS
        sum_delta_soc = 0

        # iterate over trips of vehicle
        for idx, row in enumerate(v_id_list):
            departure_event_in_input = True
            arrival = row["arrival_time"]
            arrival = datetime.datetime.strptime(arrival, DATETIME_FORMAT)
            try:
                departure = v_id_list[idx + 1]["departure_time"]
                departure = datetime.datetime.strptime(departure, DATETIME_FORMAT)
                next_arrival = v_id_list[idx + 1]["arrival_time"]
                next_arrival = datetime.datetime.strptime(next_arrival, DATETIME_FORMAT)
            except IndexError:
                departure_event_in_input = False
                departure = arrival + datetime.timedelta(hours=8)

            # check if column delta_soc or column soc exists
            if "delta_soc" not in row.keys():
                if "soc" in row.keys():
                    csv_start_soc = float(row["soc"])
                    delta_soc = 1 - csv_start_soc
                    # might want to avoid very low battery levels (configurable in config)
                    soc_threshold = args.min_soc_threshold
                    if csv_start_soc < soc_threshold:
                        warnings.warn(f"CSV contains very low SoC for '{v_id}' "
                                      f"in row {idx + 1}.")
                else:
                    # get vehicle infos
                    capacity = vehicle_types[v_type]["capacity"]
                    try:
                        # convert mileage per 100 km in 1 km
                        mileage = vehicle_types[v_type]["mileage"] / 100
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

            # might want to avoid very low battery levels (configurable in config)
            soc_threshold = args.min_soc_threshold
            if (1 - delta_soc) < soc_threshold:
                warnings.warn(f"CSV contains very high energy demand for '{v_id}' "
                              f"in row {idx + 1}.")

            sum_delta_soc += delta_soc

            if int(row["connect_cs"]) == 1:
                connect_cs = "CS_" + v_id
            else:
                connect_cs = None

            if departure < arrival:
                warnings.warn(f"{arrival}: {v_id} travelling in time (departing {departure}).")

            # adjust SoC if sum_delta_soc > min_soc
            if connect_cs is not None:
                if args.min_soc < sum_delta_soc:
                    trips_above_min_soc += 1
                    if last_arrival_event is None:
                        # initially unconnected: adjust initial SoC
                        vehicles[v_id]["soc"] = sum_delta_soc
                    else:
                        # adjust last event reference
                        last_arrival_event["update"]["desired_soc"] = sum_delta_soc
                trips_total += 1

                if sum_delta_soc > 1:
                    warnings.warn(
                        f"Problem at {arrival.isoformat()}: vehicle {v_id} of type {v_type} used "
                        f"{round(sum_delta_soc * 100, 2)} % of its battery capacity.")

                last_arrival_event = {
                    "signal_time": arrival.isoformat(),
                    "start_time": arrival.isoformat(),
                    "vehicle_id": v_id,
                    "event_type": "arrival",
                    "update": {
                        "connected_charging_station": connect_cs,
                        "estimated_time_of_departure": departure.isoformat(),
                        "soc_delta": -sum_delta_soc,
                        "desired_soc": args.min_soc,
                    }
                }
                # reset sum_delta_soc to start adding up again until connected to next CS
                sum_delta_soc = 0

                events["vehicle_events"].append(last_arrival_event)

                if departure_event_in_input:
                    if departure > next_arrival:
                        warnings.warn(f"{departure}: {v_id} travelling in time "
                                      f"(arriving {next_arrival}).")

                    events["vehicle_events"].append({
                        "signal_time": departure.isoformat(),
                        "start_time": departure.isoformat(),
                        "vehicle_id": v_id,
                        "event_type": "departure",
                        "update": {
                            "estimated_time_of_arrival":  next_arrival.isoformat(),
                        }
                    })

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
    # save path and options for CSV timeseries
    times = []
    for row in input:
        times.append(row["departure_time"])
    times.sort()
    start = times[0]
    start = datetime.datetime.strptime(start, DATETIME_FORMAT)
    stop = start + datetime.timedelta(days=args.days)

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
            "start_time": start.replace(microsecond=0).isoformat(),
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
            "start_time": start.replace(microsecond=0).isoformat(),
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

    # check voltage level (used in cost calculation)
    voltage_level = vars(args).get("voltage_level")
    if voltage_level is None:
        warnings.warn("Voltage level is not set, please choose one when calculating costs.")

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


def assign_vehicle_id(input, vehicle_types, export=None):
    """
    Assigns all rotations to specific vehicles with distinct vehicle_id. The assignment follows the
    principle "first in, first out". The assignment of a minimum standing time in hours is optional.

    :param input: schedule of rotations
    :type input: dict
    :param vehicle_types: dict with vehicle types
    :type vehicle_types: dict
    :param export: path to output file of input with vehicle_id
    :type export: str or None
    :return: schedule of rotations
    :rtype: dict
    """

    # rotations in progress: ordered by next possible departure time
    rotations_in_progress = []
    # list of currently idle vehicles
    idle_vehicles = []
    # keep track of number of needed vehicles per type
    v_type_counts = {v_type: 0 for v_type in vehicle_types.keys()}

    # calculate min_standing_time at a charging station for each vehicle type
    # CS power is identical for all vehicles per type: maximum of loading curve
    cs_power = {v_type: max([v[1] for v in v_info["charging_curve"]])
                for v_type, v_info in vehicle_types.items()}
    min_standing_times = {
        v_type: datetime.timedelta(hours=(
            v_info["capacity"] / cs_power[v_type]
        )) for v_type, v_info in vehicle_types.items()}

    # sort rotations by departure time
    rotations = sorted(input, key=lambda d: d.get('departure_time'))

    # find vehicle for each rotation
    for rot in rotations:
        arrival_time = datetime.datetime.strptime(rot["arrival_time"], DATETIME_FORMAT)
        departure_time = datetime.datetime.strptime(rot["departure_time"], DATETIME_FORMAT)
        while rotations_in_progress:
            # find vehicles that have completed rotation and stood for a minimum standing time
            # mark those vehicle as idle

            # get first rotation in progress
            r = rotations_in_progress.pop(0)

            # min_departure_time computed when inserting rotation (see below)
            if departure_time > r["min_departure_time"]:
                # standing time sufficient: vehicle idle, no longer in progress
                idle_vehicles.append(r["vehicle_id"])
            else:
                # not arrived or standing time not sufficient:
                # prepend to rotation again
                rotations_in_progress.insert(0, r)
                # ordered by possible departure time: other rotations not possible as well
                break

        # find idle vehicle for rotation if exists, else generate new vehicle id
        v_type = rot["vehicle_type"]
        try:
            # find idle vehicle for rotation
            v_id = next(v_id for v_id in idle_vehicles if v_type in v_id)
            idle_vehicles.remove(v_id)
        except StopIteration:
            # no vehicle idle: generate new vehicle id
            v_type_counts[v_type] += 1
            v_id = f"{v_type}_{v_type_counts[v_type]}"

        rot["vehicle_id"] = v_id
        # insert new rotation into list of ongoing rotations
        # calculate earliest possible new departure time
        min_departure_time = arrival_time + min_standing_times[v_type]
        # find place to insert
        i = 0
        for i, r in enumerate(rotations_in_progress):
            # go through rotations in order, stop at same or higher departure
            if r["min_departure_time"] >= min_departure_time:
                break
        rot["min_departure_time"] = min_departure_time
        # insert at calculated index
        rotations_in_progress.insert(i, rot)

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
    parser.add_argument('--days', metavar='N', type=int, default=7,
                        help='set duration of scenario as number of days')

    # general
    parser.add_argument('--interval', metavar='MIN', type=int, default=15,
                        help='set number of minutes for each timestep (Δt)')
    parser.add_argument('--min-soc', metavar='SOC', type=float, default=0.8,
                        help='set minimum desired SOC (0 - 1) for each charging process')
    parser.add_argument('--min-soc-threshold', type=float, default=0.05,
                        help='SoC below this threshold trigger a warning. Default: 0.05')
    parser.add_argument('--battery', '-b', default=[], nargs=2, type=float, action='append',
                        help='add battery with specified capacity in kWh and C-rate \
                        (-1 for variable capacity, second argument is fixed power))')
    parser.add_argument('--gc-power', type=float, default=100, help='set power at grid connection '
                                                                    'point in kW')
    parser.add_argument('--voltage-level', '-vl', help='Choose voltage level for cost calculation')
    parser.add_argument('--pv-power', type=int, default=0, help='set nominal power for local '
                                                                'photovoltaic power plant in kWp')
    parser.add_argument('--cs-power-min', type=float, default=None,
                        help='set minimal power at charging station in kW (default: 0.1 * cs_power')
    parser.add_argument('--discharge-limit', default=0.5,
                        help='Minimum SoC to discharge to during V2G. [0-1]')
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
    parser.add_argument('--export-vehicle-id-csv', default=None,
                        help='option to export csv after assigning vehicle_id')

    # config
    parser.add_argument('--config', help='Use config file to set arguments')

    args = parser.parse_args()

    set_options_from_config(args, check=False, verbose=False)

    generate_from_csv(args)
