#!/usr/bin/env python3

import argparse
import datetime
import json
import random
# from math import exp, log

from util import datetime_from_isoformat

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Generate example JSON for Netz_eLOG modelling')
    parser.add_argument('output', help='output file name')
    parser.add_argument('--external_csv', nargs='?', help='generate CSV for external load. Not implemented.')
    parser.add_argument('--include_csv', nargs='*', help='include CSV for external load. You may define custom options in the form option=value')
    parser.add_argument('--cars', metavar='N', type=int, default=8, help='set number of cars')
    parser.add_argument('--days', metavar='N', type=int, default=30, help='set number of days to create')
    parser.add_argument('--interval', metavar='MIN', type=int, default=15, help='set number of minutes for each timestep')
    args = parser.parse_args()

    start = datetime.datetime(year=2020, month=1, day=1, tzinfo=datetime.timezone(datetime.timedelta(hours=2)))
    stop  = start + datetime.timedelta(days=args.days)
    interval = datetime.timedelta(minutes=args.interval)

    # CONSTANTS

    num_car_type_1 = int(args.cars * (5/8))
    num_car_type_2 = args.cars - num_car_type_1
    avg_distance = 40 # km
    std_distance = 2.155

    # VEHICLE TYPES
    vehicle_types = {
        "sprinter": {
            "name": "sprinter",
            "capacity": 70, # kWh
            "mileage": 35, # kWh / 100km
            "max_charging_power": 7, # kWh
            "charging_curve": [[0, 7], [80, 7], [100, 0]], # SOC -> kWh
            "count": num_car_type_1
        },
        "golf": {
            "name": "E-Golf",
            "capacity": 50,
            "mileage": 16,
            "max_charging_power": 22,
            "charging_curve": [[0, 22], [80, 22], [100, 0]],
            "count": num_car_type_2
        }
    }

    # VEHICLES WITH THEIR CHARGING STATION
    vehicles = {}
    charging_stations = {}
    for name, t in vehicle_types.items():
        for i in range(t["count"]):
            v_name = "{}_{}".format(name, i)
            cs_name = "CS_" + v_name
            is_connected = True
            depart = start + datetime.timedelta(hours=6, minutes=15 * random.randint(0,4))
            desired_soc = 100
            soc = random.randint(50,100)
            vehicles[v_name] = {
                "connected_charging_station": cs_name,
                "estimated_time_of_departure": depart.isoformat(),
                "desired_soc": desired_soc,
                "soc": soc,
                "vehicle_type": name
            }

            charging_stations[cs_name] = {
                "max_power": t['max_charging_power'],
                "parent": "GC1"
            }

    events = {
        "grid_operator_signals": [
            {
                "signal_time": start.isoformat(),
                "grid_connector_id": "GC1",
                "start_time": start.isoformat(),
                "max_power": None,
                "cost": {
                    "type": "polynomial",
                    "value": [1.0, 0.0, 1.0]
                }
            },
            # {
                # "signal_time": "2019-12-31T23:00:00+02:00",
                # "grid_connector_id": "GC1",
                # "start_time": "2020-01-01T00:15:00+02:00",
                # "max_power": None,
                # "cost": {
                    # "fixed": 100
                # }
            # }
        ],
        "external_load": {},
        "vehicle_events": []
    }

    """
    now = start

    if args.external_csv:
        csv_file = open(args.external_csv, 'w')
        csv_file.write("datetime, energy\n")

    while now < stop:
        now += interval

        if args.external_csv and now.minute == 0:
            csv_file.write("{},{}\n".format(now.isoformat(), 0))


    if args.external_csv:
        csv_file.close()

    """

    if args.include_csv:
        filename = args.include_csv.pop(0)
        basename = filename.split('.')[0]
        options = {
            "csv_file": filename,
            "start_time": start.isoformat(),
            "step_duration_s": 900, # 15 minutes
            "grid_connector_id": "GC1",
            "column": "energy"
        }
        for opt in args.include_csv:
            k,v = opt.split('=')
            options[k] = v
        events['external_load'][basename] = options

    daily  = datetime.timedelta(days=1)
    hourly = datetime.timedelta(hours=1)

    # create vehicle events
    # each day, each vehicle leaves between 6 and 7 and returns after using some battery power

    now = start
    while now < stop:
        for v_id, v in vehicles.items():
            capacity = vehicle_types[v["vehicle_type"]]["capacity"]
            mileage = vehicle_types[v["vehicle_type"]]["mileage"]

            # departure
            # dep_time = datetime.datetime.fromisoformat(v["estimated_time_of_departure"])
            dep_time = datetime_from_isoformat(v["estimated_time_of_departure"])
            # first day is holiday
            distance = v.get("distance", 0)
            soc_delta = distance * mileage / capacity
            if distance:
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
            else:
                arrival_time = dep_time

            #arrival
            # plan next day
            dep_time = now + daily + datetime.timedelta(hours=6, minutes=15 * random.randint(0,4))
            distance = random.gauss(avg_distance, std_distance)
            distance = min(max(17, distance), 120)
            soc_needed = distance * mileage / capacity
            v['distance'] = distance
            v["estimated_time_of_departure"] = dep_time.isoformat()

            events["vehicle_events"].append({
                "signal_time": arrival_time.isoformat(),
                "start_time": arrival_time.isoformat(),
                "vehicle_id": v_id,
                "event_type": "arrival",
                "update": {
                    "connected_charging_station": "CS_" + v_id,
                    "estimated_time_of_departure": dep_time.isoformat(),
                    "desired_soc": 100, #soc_needed * 1.1,
                    "soc_delta": -soc_delta
                }
            })

            """

            dep_time = now + datetime.timedelta(hours=6, minutes = 15 * random.randint(0,4))
            soc = v.get("next_soc", v["desired_soc"])
            capacity = vehicle_types[v["vehicle_type"]]["capacity"]
            soc_delta = random.randint(10, 30)
            t_delta = datetime.timedelta(hours=random.randint(6,8), minutes=random.randint(0, 59))
            t_delta = t_delta - datetime.timedelta(microseconds=t_delta.microseconds)
            arrival_time = dep_time + t_delta
            # print(dep_time, t_delta, arrival_time)

            events["vehicle_events"].append({
                "signal_time": now.isoformat(),
                "start_time": dep_time.isoformat(),
                "vehicle_id": v_id,
                "event_type": "departure",
                "update": {
                    "estimated_time_of_arrival": arrival_time.isoformat()
                }
            })

            #arrival
            v["next_soc"] = 100
            events["vehicle_events"].append({
                "signal_time": arrival_time.isoformat(),
                "start_time": arrival_time.isoformat(),
                "vehicle_id": v_id,
                "event_type": "arrival",
                "update": {
                    "connected_charging_station": "CS_" + v_id,
                    "estimated_time_of_departure": dep_time.isoformat(),
                    "desired_soc": v["next_soc"],
                    "soc_delta": -soc_delta
                }
            })
            """

        # next day
        now += daily

    # reset initial SOC
    for v in vehicles.values():
        del v["distance"]

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
                  "max_power": 2000
                }
            },
            "charging_stations": charging_stations
        },
        "events": events
    }

    # Write JSON
    with open(args.output, 'w') as f:
        json.dump(j, f, indent=2)
