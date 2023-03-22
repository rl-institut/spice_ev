#!/usr/bin/env python3

import csv
import datetime
from pathlib import Path
import random
import warnings


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
    :return: scenario
    :rtype: dict
    """

    # set seed
    random.seed(args.seed)

    # define interval for simulation
    interval = datetime.timedelta(minutes=args.interval)
    # read csv input file
    input = csv_to_dict(args.input_file)

    # save path and options for CSV timeseries
    times = []
    for row in input:
        times.append(row["departure_time"])
    times.sort()
    start = times[0]
    start = datetime.datetime.strptime(start, DATETIME_FORMAT)
    stop = start + datetime.timedelta(days=args.days)

    # INITIALIZE COMPONENTS AND EVENTS
    vehicle_types = {}
    vehicles = {}
    charging_stations = {}
    events = {
        "grid_operator_signals": [],
        "fixed_load": {},
        "local_generation": {},
        "vehicle_events": []
    }

    # count number of trips for which desired_soc is above min_soc
    trips_above_min_soc = 0
    trips_total = 0

    # set vehicle type if present in vehicle_types.json
    for v_type in {item['vehicle_type'] for item in input}:
        try:
            vehicle_types.update({v_type: args.predefined_vehicle_types[v_type]})
        except KeyError:
            print(f"The vehicle type '{v_type}' defined in the input csv cannot be found in "
                  f"vehicle_types.json. Please check for consistency.")

    # check input file for column 'vehicle_id'
    if "vehicle_id" not in input[0].keys():
        if args.verbose > 0:
            warnings.warn("Column 'vehicle_id' missing, vehicles are assigned by the principle "
                          "first in, first out.")
        if args.export_vehicle_id_csv is not None:
            export_filename = Path(args.output).parent / args.export_vehicle_id_csv
        else:
            export_filename = None
        input = assign_vehicle_id(input, vehicle_types, export_filename)

    # check input file for column 'connect_cs'
    if "connect_cs" not in input[0].keys():
        if args.verbose > 0:
            warnings.warn("Column 'connect_cs' is not available. Vehicles will be connected to a "
                          "charging station after every trip.")
        input = [dict(item, **{'connect_cs': 1}) for item in input]

    # GENERATE VEHICLE EVENTS: iterate over input file
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
            "min_power": args.cs_power_min if args.cs_power_min is not None else 0.1 * cs_power,
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
                # no departure: stand for 8h or until end of simulation (whichever comes later)
                departure_event_in_input = False
                departure = max(arrival + datetime.timedelta(hours=8), stop)

            # check if column delta_soc or column soc exists
            if "delta_soc" not in row.keys():
                if "soc" in row.keys():
                    csv_start_soc = float(row["soc"])
                    delta_soc = 1 - csv_start_soc
                    # might want to avoid very low battery levels (configurable in config)
                    if csv_start_soc < args.min_soc_threshold and args.verbose > 0:
                        warnings.warn(f"CSV contains very low SoC for '{v_id}' "
                                      f"in row {idx + 1}.")
                else:
                    # get vehicle infos
                    capacity = vehicle_types[v_type]["capacity"]
                    try:
                        # convert mileage per 100 km in 1 km
                        mileage = vehicle_types[v_type]["mileage"] / 100
                    except ValueError:
                        warnings.warn("In order to assign the vehicle consumption, either a "
                                      "mileage must be given in vehicle_types.json or a soc or "
                                      "delta_soc must be given in the input file. "
                                      "Please check for consistency.")
                    try:
                        distance = float(row["distance"])
                    except ValueError:
                        warnings.warn("In order to assign the vehicle consumption via the mileage, "
                                      "the column 'distance' must be given in the input csv. "
                                      "Please check for consistency.")
                    delta_soc = distance * mileage / capacity
            else:
                delta_soc = float(row["delta_soc"])

            # might want to avoid very low battery levels (configurable in config)
            if (1 - delta_soc) < args.min_soc_threshold and args.verbose > 0:
                warnings.warn(f"CSV contains very high energy demand for '{v_id}' "
                              f"in row {idx + 1}.")

            sum_delta_soc += delta_soc

            if int(row["connect_cs"]) == 1:
                connect_cs = "CS_" + v_id
            else:
                connect_cs = None

            if departure < arrival:
                warnings.warn(f"{arrival}: {v_id} travelling in time (departing {departure}).")

            # arrival at new CS
            if connect_cs is not None:
                # adjust SoC if sum_delta_soc > min_soc
                if args.min_soc < sum_delta_soc:
                    trips_above_min_soc += 1
                    if last_arrival_event is None:
                        # initially unconnected: adjust initial SoC
                        vehicles[v_id]["soc"] = sum_delta_soc
                    else:
                        # update last charge event info: set desired SOC
                        last_arrival_event["update"]["desired_soc"] = sum_delta_soc
                trips_total += 1

                if sum_delta_soc > 1:
                    warnings.warn(
                        f"Problem at {arrival.isoformat()}: vehicle {v_id} of type {v_type} used "
                        f"{round(sum_delta_soc * 100, 2)} % of its battery capacity.")

                # update last charge event info
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
                events["vehicle_events"].append(last_arrival_event)

                # reset sum_delta_soc to start adding up again until connected to next CS
                sum_delta_soc = 0

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

    # update info of external CSV files
    ext_info = {
        "fixed_load": "include_fixed_load_csv",
        "local_generation": "include_local_generation_csv",
        "energy_price_from_csv": "include_price_csv",
    }
    for info, field in ext_info.items():
        option = field + "_option"
        if vars(args)[field] and vars(args)[option]["start_time"] is None:
            vars(args)[option]["start_time"] = start.isoformat()
        if vars(args)[option]:
            if info == "energy_price_from_csv":
                events[info] = vars(args)[option]
            else:
                events[info][vars(args)[field]] = vars(args)[option]

    if args.include_price_csv is None:
        # generate daily price evens
        daily = datetime.timedelta(days=1)
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

    # number of trips for which desired_soc is above min_soc
    if trips_above_min_soc and args.verbose > 0:
        print(f"{trips_above_min_soc} of {trips_total} trips "
              f"use more than {args.min_soc * 100}% capacity")

    return {
        "scenario": {
            "start_time": start.isoformat(),
            "interval": interval.total_seconds() // 60,
            "stop_time": stop.isoformat(),
            "discharge_limit": args.discharge_limit,
        },
        "components": {
            "vehicle_types": vehicle_types,
            "vehicles": vehicles,
            "grid_connectors": args.gc,
            "charging_stations": charging_stations,
            "batteries": args.battery,
            "photovoltaics": args.pv,
        },
        "events": events,
    }


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
