#!/usr/bin/env python3

import argparse
import csv
import datetime
import json
import random
import numpy as np
from os import path

from src.util import set_options_from_config


def generate_opp_trips_from_schedule(args):
    """Generate a scenario JSON from rotation schedule csv or JSON of trips with OPP options.
    args: argparse.Namespace
    """
    missing = [arg for arg in ["input_file", "output"] if vars(args).get(arg) is None]
    if missing:
        raise SystemExit("The following arguments are required: {}".format(", ".join(missing)))

    # load input file
    input_ext = args.input_file.split(".")[-1]
    # read csv file
    if input_ext == "csv":
        print("Convert csv file to json")
        input_json = convert_csv_to_json(args)
        input = json.loads(input_json)
    elif input_ext == "json":
        # read JSON file
        with open(args.input_file) as json_file:
            input = json.load(json_file)
    else:
        print("The input file type is unknown. Please insert a path to a csv or json file.")

    interval = datetime.timedelta(minutes=args.interval)

    # load predefined vehicle dict
    if args.vehicle_types is None:
        args.vehicle_types = "examples/vehicle_types.json"
        print("No definition of vehicle types found, using {}".format(args.vehicle_types))
    ext = args.vehicle_types.split('.')[-1]
    if ext != "json":
        print("File extension mismatch: vehicle type file should be .json")
    with open(args.vehicle_types) as f:
        predefined_vehicle_types = json.load(f)

    # load stations file
    if args.electrified_stations is None:
        args.electrified_stations = "examples/electrified_stations.json"
    ext = args.vehicle_types.split('.')[-1]
    if ext != "json":
        print("File extension mismatch: electrified_stations file should be .json")
    with open(args.electrified_stations) as json_file:
        stations_dict = json.load(json_file)
    # get list of all stations, independent of OPP and D
    stations = []
    for key in stations_dict.keys():
        for key2 in stations_dict[key].keys():
            stations.append(key2)

    vehicle_types = {}
    vehicles = {}
    batteries = {}
    charging_stations = {}
    grid_connectors = {}
    events = {
        "grid_operator_signals": [],
        "external_load": {},
        "energy_feed_in": {},
        "vehicle_events": []
    }
    # set default charging type
    if args.preferred_ct == "depot":
        ct = "depot"
    else:
        ct = "opp"
    # update vehicle type and add vt from predefined vehicle type dict
    vt_keys = set(d['vehicle_type'] for d in input.values())
    for vehicle_type in vt_keys:
        vt_ct = vehicle_type + "_" + ct
        # update vehicle types with vehicles in input csv
        try:
            vehicle_types.update({vt_ct: predefined_vehicle_types[vt_ct]})
        except KeyError:
            print(f"The vehicle type {vehicle_type} defined in the input csv cannot be found in "
                  f"vehicle_types.json. Please check for consistency.")
    # add default charging type to all rotations
    for rotation in input:
        input[rotation]["vehicle_type"] = input[rotation]["vehicle_type"] + "_" + ct

    # calculate first energy consumption and soc of all trips
    input = add_delta_soc(input, vehicle_types, args)

    # if args.prefered_ct is depot: only add opp charging if consumption is higher that capacity
    if args.preferred_ct == "depot":
        for rotation in input.keys():
            vt = input[rotation]["vehicle_type"]
            if input[rotation]["consumption"] > vehicle_types[vt]["capacity"]:
                vt_ct = input[rotation]["vehicle_type"].split("_")[0] + "_opp"
                input[rotation]["vehicle_type"] = vt_ct
                # get vehicle type with OPP charging from predefined vehicle type dict
                try:
                    vehicle_types.update({vt_ct: predefined_vehicle_types[vt_ct]})
                except KeyError:
                    print(
                        f"The vehicle type {vehicle_type} defined in the input csv cannot be found "
                        f"in vehicle_types.json. Please check for consistency.")
        # recalculate soc, depending on the new charging types
        input = add_delta_soc(input, vehicle_types, args)
    # add vehicle_id (sort vehicles) depending on vt and ct
    input = add_vehicle_id(input)

    # get number of vehicles per type
    number_per_vt = {}
    vt_keys = set(d['vehicle_type'] for d in input.values())
    for vt in vt_keys:
        vt_list = {k: v for k, v in input.items() if v["vehicle_type"] == vt}
        number_per_vt[vt] = max(int(d['vehicle_id'].split("_")[-1]) for d in vt_list.values())

    # add vehicle events
    for vt in number_per_vt.keys():
        for id in range(1, number_per_vt[vt]+1):
            name = vt
            ct = vt.split("_")[1]
            v_name = "{}_{}".format(name, id)
            # define start conditions
            vehicles[v_name] = {
                "connected_charging_station": None,
                "estimated_time_of_departure": None,
                "desired_soc": None,
                "soc": args.min_soc,
                "vehicle_type": name
            }
            # filter all rides for that bus
            v_id = {k: v for k, v in input.items() if v["vehicle_id"] == v_name}
            # sort events for their departure time, so that the matching departure time of an
            # arrival event can be read out of the next element in vid_list
            v_id = {key: value for key, value in sorted(v_id.items(),
                                                        key=lambda x: x[1]['departure_time'])}
            key_list = list(v_id.keys())
            for i, v in enumerate(key_list):
                departure_event_in_input = True
                # create events for all trips of one rotation
                for j, trip in enumerate(v_id[v]["trips"]):
                    cs_name = "{}_{}".format(v_name, trip["arrival_name"])
                    gc_name = "{}".format(trip["arrival_name"])
                    arrival = trip["arrival_time"]
                    arrival = datetime.datetime.strptime(arrival, '%Y-%m-%d %H:%M:%S')
                    try:
                        departure = v_id[v]["trips"][j+1]["departure_time"]
                        departure = datetime.datetime.strptime(departure, '%Y-%m-%d %H:%M:%S')
                        next_arrival = v_id[v]["trips"][j+1]["arrival_time"]
                        next_arrival = datetime.datetime.strptime(next_arrival, '%Y-%m-%d %H:%M:%S')
                    except IndexError:
                        # get departure of the first trip of the next rotation
                        try:
                            departure = v_id[key_list[i+1]]["departure_time"]
                            departure = datetime.datetime.strptime(departure, '%Y-%m-%d %H:%M:%S')
                            next_arrival = v_id[key_list[i+1]]["trips"][0]["arrival_time"]
                            next_arrival = datetime.datetime.strptime(next_arrival,
                                                                      '%Y-%m-%d %H:%M:%S')
                        except IndexError:
                            departure_event_in_input = False
                            departure = arrival + datetime.timedelta(hours=8)
                            # no more rotations
                    # connect cs and add gc if station is electrified
                    connected_charging_station = None
                    skip = False
                    desired_soc = 0
                    # if arrival = departure time, do not connect charging station
                    if arrival != departure:
                        if gc_name in stations:
                            # if station is depot, set min_soc = args.min_soc, else: None
                            if gc_name in stations_dict["depot_stations"]:
                                desired_soc = args.min_soc
                                number_cs = stations_dict["depot_stations"][gc_name]
                                cs_power = args.cs_power_depot
                                if number_cs != "None":
                                    gc_power = number_cs * cs_power
                                else:
                                    gc_power = 100 * cs_power
                            else:
                                if ct == "depot":
                                    skip = True
                                else:
                                    number_cs = stations_dict["opp_stations"][gc_name]
                                    cs_power = args.cs_power_opp
                                    desired_soc = 1
                                    if number_cs != "None":
                                        gc_power = number_cs * cs_power
                                    else:
                                        gc_power = 100 * cs_power
                            if not skip:
                                connected_charging_station = cs_name
                                # add one charging station for each bus at bus station
                                if cs_name not in charging_stations:
                                    charging_stations[cs_name] = {
                                        "max_power": cs_power,
                                        "min_power": 0.1 * cs_power,
                                        "parent": gc_name
                                    }
                                # add one grid connector for each bus station
                                if gc_name not in grid_connectors:
                                    number_cs = None if number_cs == 'None' else number_cs
                                    grid_connectors[gc_name] = {
                                        "max_power": gc_power,
                                        "cost": {"type": "fixed", "value": 0.3},
                                        "number_cs": number_cs
                                    }

                    # create arrival events
                    events["vehicle_events"].append({
                        "signal_time": arrival.isoformat(),
                        "start_time": arrival.isoformat(),
                        "vehicle_id": v_name,
                        "event_type": "arrival",
                        "update": {
                            "connected_charging_station": connected_charging_station,
                            "estimated_time_of_departure": departure.isoformat(),
                            "soc_delta": trip["delta_soc"],
                            "desired_soc": desired_soc
                        }
                    })
                    # create departure events
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

    # define start and stop times
    times = [val["departure_time"] for key, val in input.items() if "departure_time" in val]
    start = min(times)
    start = datetime.datetime.strptime(start, '%Y-%m-%d %H:%M:%S')
    stop = start + datetime.timedelta(days=args.days)
    daily = datetime.timedelta(days=1)
    # price events
    for key in grid_connectors.keys():
        if not args.include_price_csv:
            now = start - daily
            while now < stop + 2 * daily:
                now += daily
                for v_id, v in vehicles.items():
                    if now >= stop:
                        # after end of scenario: keep generating trips, but don't include in
                        # scenario
                        continue

                # generate prices for the day
                if now < stop:
                    morning = now + datetime.timedelta(hours=6)
                    evening_by_month = now + datetime.timedelta(hours=22 - abs(6 - now.month))
                    events['grid_operator_signals'] += [{
                        # day (6-evening): 15ct
                        "signal_time": max(start, now - daily).isoformat(),
                        "grid_connector_id": key,
                        "start_time": morning.isoformat(),
                        "cost": {
                            "type": "fixed",
                            "value": 0.15 + random.gauss(0, 0.05)
                        }
                    }, {
                        # night (depending on month - 6): 5ct
                        "signal_time": max(start, now - daily).isoformat(),
                        "grid_connector_id": key,
                        "start_time": evening_by_month.isoformat(),
                        "cost": {
                            "type": "fixed",
                            "value": 0.05 + random.gauss(0, 0.03)
                        }
                    }]

    # add timeseries from csv

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



    for idx, (capacity, c_rate, gc) in enumerate(args.battery):
        if capacity > 0:
            max_power = c_rate * capacity
        else:
            # unlimited battery: set power directly
            max_power = c_rate
        batteries["BAT{}".format(idx+1)] = {
            "parent": gc,
            "capacity": capacity,
            "charging_curve": [[0, max_power], [1, max_power]]
        }
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
            "grid_connectors": grid_connectors,
            "charging_stations": charging_stations,
            "batteries": batteries
        },
        "events": events
    }
    # Write JSON
    with open(args.output, 'w') as f:
        json.dump(j, f, indent=2)


def add_delta_soc(dict, vehicle_types, args):
    """
    Wrapper for the consumption.calculate_trip_consumption() function.

    Iterates through the schedule dictionary, filters out vehicle_type and trips and saves delta_soc
    and consumption for each trip and each rotation.

    :param dict: schedule dict with all rotations and trips
    :type dict: dict
    :param vehicle_types: dict of all vehicle types represented in the schedule dict
    :type vehicle_types: dict
    :param args: input arguments
    :type args: argparse.Namespace
    :return: dictionary with consumption and delta_soc
    :rtype: dict
    """

    for vt in vehicle_types:
        # filter all rides for that bus
        vt_dict = {k: v for k, v in dict.items() if v["vehicle_type"] == vt}

        for rotation in vt_dict.keys():
            i = 0
            rotation_con = 0
            # calculate consumption of trip
            for trip in vt_dict[rotation]["trips"]:
                trip = {"distance": trip["distance"],
                        "mileage": vehicle_types[vt]["mileage"],
                        "departure_time": trip["departure_time"],
                        "arrival_time": trip["arrival_time"],
                        "pause": trip["pause"]
                        }
                trip_consumption = calculate_trip_consumption(trip, vehicle_types[vt],
                                                              vt, args.rush_hour)
                delta_soc = - (trip_consumption / vehicle_types[vt]["capacity"])
                # add info for each trip
                dict[rotation]["trips"][i]["delta_soc"] = delta_soc
                dict[rotation]["trips"][i]["consumption"] = trip_consumption
                i += 1
                rotation_con += trip_consumption
            # add total rotation consumption to dict
            dict[rotation]["consumption"] = rotation_con
    return dict


def calculate_trip_consumption(trip, vehicle_dict, vehicle_type, rush_hour=None):
    """
       Calculates delta_soc and energy consumption for one trip
       Note that this function will be extended soon. This is only a dummy function.

       :param trip: dictionary with trip information (see note for further explanation)
       :type trip: dict
       :param vehicle_dict: dictionary with information about the specific vehicle type, especially\
       capacity, mileage, hc
       :type vehicle_dict: dict
       :param vehicle_type: name of the specific vehicle type
       :type vehicle_type: str
       :param rush_hour: dict containing hours of rush hours, see
       example/generate_distributed_charging.cfg for further information.
       :type rush_hour: dict
       :return: delta_soc, total_consumption
       :rtype: tuple

       note: A trip should contain the following information:
       trip = {distance,\
               departure_time, (optional, needs to be set if you want to use rush_hour)\
               arrival_time, (optional, needs to be set if you want to use rush_hour)}\
       """
    # load temperature of the day, now dummy winter day
    winter_day = csv_to_dict("./examples/consumption_inputs/default_temp_winter_day_08_01_2018.csv")
    # todo: calculate consumption based on rush_hour, location, load etc.
    # for now a dummy consumption over temperature is loaded (SB timeseries of BVG)
    times = [row["time"] for row in winter_day]
    hours = []
    for t in times:
        dt = (datetime.datetime.strptime(t, '%H:%M:%S')).hour
        hours.append(dt)
    temperatures = [float(row["temperature in c"]) for row in winter_day]
    arrival_hour = (datetime.datetime.strptime(trip["arrival_time"], '%Y-%m-%d %H:%M:%S')).hour

    temp = np.interp(arrival_hour, hours, temperatures)

    # load consumption csv
    consumption_file = vehicle_dict["mileage"]
    consumption = csv_to_dict(consumption_file)

    xp = [float(row["temp."]) for row in consumption]
    fp = [float(row["kat. b"]) for row in consumption]

    con = np.interp(temp, xp, fp)

    return con


def add_vehicle_id(input):
    """
    Assigns all rotations to specific vehicles with distinct vehicle_id.

    :param input: schedule of rotations
    :type input: dict
    :return: schedule of rotations
    :rtype: dict
    """

    # sort rotations and add add vehicle_id
    # filter for vehicle_type
    vehicle_types = set(d['vehicle_type'] for d in input.values())
    for vt in vehicle_types:
        vt_line = {k: v for k, v in input.items() if v["vehicle_type"] == vt}
        # sort list of vehicles by departure time
        depot_stations = set(d['departure_name'] for d in vt_line.values())
        for depot in depot_stations:
            bus_number = 0
            d_line = {k: v for k, v in vt_line.items() if v["departure_name"] == depot}
            departures = {key: value for key, value in sorted(d_line.items(),
                          key=lambda x: x[1]['departure_time'])}
            arrivals = {key: value for key, value in sorted(d_line.items(),
                        key=lambda x: x[1]['arrival_time'])}
            # get the first arrival
            first_arrival_time = arrivals[list(arrivals.keys())[0]]["arrival_time"]
            for rotation in departures.keys():
                if first_arrival_time >= d_line[rotation]["departure_time"]:
                    bus_number += 1
                    input[rotation]["vehicle_id"] = vt + "_" + str(bus_number)
                elif arrivals[list(arrivals.keys())[0]]["arrival_time"] < \
                        d_line[rotation]["departure_time"]:
                    a_bus_number = arrivals[list(arrivals.keys())[0]]["vehicle_id"]
                    arrival_rotation = list(arrivals.keys())[0]
                    del arrivals[arrival_rotation]
                    input[rotation]["vehicle_id"] = a_bus_number
                else:
                    bus_number += 1
                    input[rotation]["vehicle_id"] = vt + "_" + str(bus_number)
    return input


def convert_csv_to_json(args):
    """
    Create input json for SpiceEV generate_distributed_charging.csv

    :param args: input arguments
    :type args: argparse.Namespace
    :returns: json with all rotations and trips
    :rtype: json

    note:
    The input csv is a csv file with each line resembling one trip.
    The csv file needs to contain the following columns:
    rotation_id: numeric
    line: str or int
    departure_name: str
    departure_short_name: str
    departure_day: int (1-7) (only necessary if departure_time/ arrival_time is given as hours
    departure_time: datetime ('%Y-%m-%d %H:%M:%S') or hour of a day as float.
    arrival_time: datetime ('%Y-%m-%d %H:%M:%S') or hour of a day as float.
    arrival_day: int (1-7)
    arrival_name: str
    arrival_short_name: str
    distance: numeric
    pause: int (minutes)
    vehicle_type: str
    """
    # constants
    weekdays = {"Monday": 1, "Tuesday": 2, "Wednesday": 3, "Thursday": 4,
                "Friday": 5, "Saturday": 6, "Sunday": 7}

    missing = [arg for arg in ["input_file", "output"] if vars(args).get(arg) is None]
    if missing:
        raise SystemExit("The following arguments are required: {}".format(", ".join(missing)))

    # get input file as dict
    input = csv_to_dict(input_csv_name=args.input_file)

    # convert times if given by hour
    try:
        for d in input:
            d.update((k, float(v)) for k, v in d.items() if k == "departure_time" or
                     k == "arrival_time")
        # check if days are given as int or str
        try:
            for d in input:
                d.update((k, float(v)) for k, v in d.items() if k == "departure_day" or
                         k == "arrival_day")
        except ValueError:
            try:
                for d in input:
                    d.update((k, weekdays[v]) for k, v in d.items() if k == "departure_day" or
                             k == "arrival_day")
            except KeyError:
                print("The weekday in the csv input file is not recognized. Please insert numbers "
                      "(1-7) or weekday names such as 'Monday', etc.")
        # convert day and hour to datetime
        start_date = str(args.start_date) + " 00:00:00"
        start_date = datetime.datetime.strptime(start_date, '%Y-%m-%d %H:%M:%S')
        # get weekday of start date
        start_weekday = start_date.weekday() + 1
        for d in input:
            if float(d["arrival_time"]) > 24:
                d["arrival_time"] = float(d["arrival_time"]) - 24
            for key in ["departure_day", "arrival_day"]:
                key2 = "departure_time" if key == "departure_day" else "arrival_time"
                if d[key] == start_weekday:
                    shift_day = 0
                elif d[key] > start_weekday:
                    shift_day = d[key] - start_weekday
                else:
                    shift_day = 7 - start_weekday + d[key]

                d.update((k, (start_date + datetime.timedelta(days=shift_day) +
                              datetime.timedelta(hours=float(d[key2])))) for k, v in
                         d.items() if k == key2)
    except ValueError:
        try:
            for d in input:
                d.update((k, datetime.datetime.strptime(v, '%Y-%m-%d %H:%M:%S')) for
                         k, v in d.items() if k == "departure_time" or k == "arrival_time")
        except ValueError:
            print("The departure_time and arrival_time format is not recognized. Please insert in "
                  "datetime format ('%Y-%m-%d %H:%M:%S') or as hour of the day in float.")

    # create dict with each rotation as a key
    unique_rotation_ids = set(d['rotation_id'] for d in input)
    schedule = {}
    for rotation_id in unique_rotation_ids:
        # filter input for rotation
        r_list = list(filter(lambda d: d['rotation_id'] in rotation_id, input))
        # if arrival weekday < departure weekday, shift one week
        list_days = [d['arrival_time'] for d in r_list]
        if any(i < r_list[0]["departure_time"] for i in list_days):
            for i, row in enumerate(r_list):
                if r_list[i]["departure_time"] < r_list[0]["departure_time"]:
                    r_list[i].update((k, v + datetime.timedelta(days=2)) for k, v in d.items() if
                                     k == "departure_time")
                if r_list[i]["arrival_time"] < r_list[0]["departure_time"]:
                    r_list[i].update((k, v + datetime.timedelta(days=2)) for k, v in d.items() if
                                     k == "arrival_time")
        r_departure = min(item['departure_time'] for item in r_list)
        r_arrival = max(item['arrival_time'] for item in r_list)

        schedule[rotation_id] = {
            "departure_time": r_departure,
            "arrival_time": r_arrival,
            "distance": sum(float(d.get('distance', 0)) for d in r_list),
            "vehicle_type": r_list[0]["vehicle_type"],
            "departure_name": r_list[0]["departure_name"],
            "arrival_name": r_list[-1]["arrival_name"],
            "trips": r_list
        }

    schedule = json.dumps(schedule, default=converter, ensure_ascii=False)
    return schedule


def converter(o):
    if isinstance(o, datetime.datetime):
        return o.__str__()


def csv_to_dict(input_csv_name):
    """
    Reads csv file and returns a dictionary

    :param csv_path: path to csv file
    :type csv_path: str
    :return: dictionary
    :rtype: dict
    """
    dict = []
    with open(input_csv_name, 'r') as file:
        reader = csv.reader(file)
        # set column names using first row
        columns = next(reader)
        # convert csv to json
        for row in reader:
            row_data = {}
            for i in range(len(row)):
                row_key = columns[i].lower()
                row_data[row_key] = row[i]
            # add data to json store
            dict.append(row_data)
    return dict


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Generate scenarios as JSON files for vehicle charging modelling')
    parser.add_argument('input_file', nargs='?',
                        help='input file name (rotations_example_table.csv)',
                        default='examples/bus_schedule.json')
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
    parser.add_argument('--start_date', default='2018-01-02',
                        help='Provide start date of simulation in format YYYY-MM-DD.E.g. '
                             '2018-01-31')
    parser.add_argument('--electrified_stations', help='include electrified_stations json',
                        default='examples/electrified_stations.json')
    parser.add_argument('--vehicle_types', help='include vehicle_types json',
                        default='examples/vehicle_types.json')
    parser.add_argument('--config', help='Use config file to set arguments',
                        default='examples/generate_distributed_charging.cfg')

    args = parser.parse_args()

    set_options_from_config(args, check=False, verbose=False)

    generate_opp_trips_from_schedule(args)
