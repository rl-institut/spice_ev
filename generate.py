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
    parser.add_argument('--desired-soc', type=int, default=None, help='set desired SOC')
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
            "mileage": 40, # kWh / 100km
            "charging_curve": [[0, 11], [80, 11], [100, 0]], # SOC -> kWh
            "min_charging_power": 0,
            "count": num_car_type_1
        },
        "golf": {
            "name": "E-Golf",
            "capacity": 50,
            "mileage": 16,
            "charging_curve": [[0, 22], [80, 22], [100, 0]],
            "min_charging_power": 0,
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
            depart = start + datetime.timedelta(days=1, hours=6, minutes=15 * random.randint(0,4))
            desired_soc = args.desired_soc or 100
            soc = random.randint(50,100)
            vehicles[v_name] = {
                "connected_charging_station": cs_name,
                "estimated_time_of_departure": depart.isoformat(),
                "desired_soc": desired_soc,
                "soc": soc,
                "vehicle_type": name
            }

            charging_stations[cs_name] = {
                "max_power": max([v[1] for v in t['charging_curve']]),
                "parent": "GC1"
            }

    events = {
        "grid_operator_signals": [
            {
                "signal_time": start.isoformat(),
                "grid_connector_id": "GC1",
                "start_time": start.isoformat(),
                "cost": {
                    "type": "polynomial",
                    "value": [0.0, 0.32, 0.0]
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
        # next day. First day is off
        now += daily

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
        },{
            # night (depending on month - 6): 5ct
            "signal_time": now.isoformat(),
            "grid_connector_id": "GC1",
            "start_time": (now + datetime.timedelta(days=1, hours=22-abs(6-now.month))).isoformat(),
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
            dep_str  = v.get('departure', v["estimated_time_of_departure"])
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
                next_dep_time = now + datetime.timedelta(days=2, hours=6, minutes=15 * random.randint(0,4))
            else:
                next_dep_time = now + datetime.timedelta(days=1, hours=6, minutes=15 * random.randint(0,4))

            next_distance = random.gauss(avg_distance, std_distance)
            next_distance = min(max(17, next_distance), 120)
            soc_needed = next_distance * mileage / capacity
            v['distance'] = next_distance
            v["departure"] = next_dep_time.isoformat()

            events["vehicle_events"].append({
                "signal_time": arrival_time.isoformat(),
                "start_time": arrival_time.isoformat(),
                "vehicle_id": v_id,
                "event_type": "arrival",
                "update": {
                    "connected_charging_station": "CS_" + v_id,
                    "estimated_time_of_departure": next_dep_time.isoformat(),
                    "desired_soc": args.desired_soc or (soc_needed * 1.1),
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
            "charging_stations": charging_stations
        },
        "events": events
    }

    # Write JSON
    with open(args.output, 'w') as f:
        json.dump(j, f, indent=2)
