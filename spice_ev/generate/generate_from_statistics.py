#!/usr/bin/env python3

import datetime
import random
import warnings

from spice_ev.util import datetime_from_isoformat


DEFAULT_START_TIME = "2023-01-01T01:00:00+02:00"


def datetime_from_string(s):
    h, m = map(int, s.split(':'))
    return datetime.datetime(1972, 1, 1, h, m)


def generate_trip(v_type_info):
    """ Create randomly generated trips from average input arguments.

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


def generate_from_statistics(args):
    """ Generate a scenario JSON from input parameters.

    :param args: input arguments
    :type args: argparse.Namespace
    :return: scenario
    :rtype: dict
    """

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

    # use default vehicles, if args.vehicles does not exist
    if args.vehicles is None:
        args.vehicles = [['1', 'golf'], ['1', 'sprinter']]

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
    for count, v_type in args.vehicles:
        assert v_type in args.predefined_vehicle_types, \
            f"The given vehicle type '{v_type}' is not valid. " \
            f"Should be one of {list(args.predefined_vehicle_types.keys())}."
        vehicle_types.update({v_type: args.predefined_vehicle_types[v_type]})
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
                "min_power": args.cs_power_min if args.cs_power_min is not None else 0.1 * cs_power,
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

            # add buffer on top of soc_delta
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
    for v_info in vehicle_types.values():
        del v_info["count"]
        del v_info["statistical_values"]

    # update info of external CSV files
    ext_info = {
        "fixed_load": "include_fixed_load_csv",
        "local_generation": "include_local_generation_csv",
        "energy_price_from_csv": "include_price_csv",
    }
    for info, field in ext_info.items():
        option = vars(args)[field + "_option"]
        field = vars(args)[field]
        if field is None:
            continue
        if option["start_time"] is None:
            option["start_time"] = start.isoformat()
        if info == "energy_price_from_csv":
            events[info] = option
        else:
            events[info][field] = option

    if args.include_price_csv is None:
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
                        "value": round(0.15 + random.gauss(0, 0.05), 5)
                    }
                }, {
                    # night (depending on month - 6): 5ct
                    "signal_time": max(start, now - daily).isoformat(),
                    "grid_connector_id": "GC1",
                    "start_time": evening_by_month.isoformat(),
                    "cost": {
                        "type": "fixed",
                        "value": round(0.05 + random.gauss(0, 0.03), 5)
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
