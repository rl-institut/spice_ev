#!/usr/bin/env python3

import argparse
import datetime
import json
import random


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Generate example JSON for Netz_eLOG modelling')
    parser.add_argument('output', help='output file name')
    parser.add_argument('external_csv', nargs='?', help='generate CSV for external load')
    args = parser.parse_args()

    start = datetime.datetime(year=2020, month=1, day=1, tzinfo=datetime.timezone(datetime.timedelta(hours=2)))
    stop  = datetime.datetime(year=2020, month=2, day=1, tzinfo=datetime.timezone(datetime.timedelta(hours=2)))
    interval = datetime.timedelta(minutes=15)

    # CONSTANTS

    # VEHICLE TYPES
    vehicle_types = {
        "sprinter": {
            "name": "sprinter",
            "capacity": 70,
            "max_charging_power": 7,
            "charging_curve": {"TODO": 42},
            "count": 5
        },
        "golf": {
            "name": "E-Golf",
            "capacity": 50,
            "max_charging_power": 22,
            "count": 3
        }
    }

    # VEHICLES WITH THEIR CHARGING STATION
    vehicles = {}
    charging_stations = {}
    for name, t in vehicle_types.items():
        for i in range(t["count"]):
            v_name = "{}_{}".format(name, i)
            cs_name = "CS_" + v_name
            is_connected = random.choice([True, False])
            depart = start + datetime.timedelta(hours=6, minutes=15 * random.randint(0,4))
            desired_soc = random.randint(50,100)
            soc = random.randint(0,100)
            vehicles[v_name] = {
                "connected_charging_station": cs_name if is_connected else None,
                "estimated_time_of_departure": depart.isoformat(),
                "desired_soc": desired_soc,
                "soc": soc,
                "vehicle_type": name
            }

            charging_stations[cs_name] = {
                "max_power": random.randint(30,300),
                "parent": "L3"
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

    daily  = datetime.timedelta(days=1)
    hourly = datetime.timedelta(hours=1)

    # create vehicle events
    # each day, each vehicle leaves between 6 and 7 and returns after using some battery power

    now = start
    while now < stop:
        for v_id, v in vehicles.items():
            # departure

            dep_time = now + datetime.timedelta(hours=6, minutes = 15 * random.randint(0,4))
            soc = v.get("next_soc", v["desired_soc"])
            capacity = vehicle_types[v["vehicle_type"]]["capacity"]
            soc_delta = random.randint(int(capacity/3), int(soc / 100 * capacity))
            t_delta = datetime.timedelta(hours = 8 * soc_delta/capacity / 0.75)
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
            v["next_soc"] = random.randint(50,100)
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

        # next day
        now += daily

    # reset initial SOC
    for v in vehicles.values():
        del v["next_soc"]

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
            "load_management": {
                # "L1": {
                    # "charging_stations": ["C1", "C2"],
                    # "parent": "L3"
                # },
                # "L2": {
                    # "charging_stations": ["C3"],
                    # "parent": "L3"
                # },
                "L3": {
                    "charging_stations": list(charging_stations.keys()),
                    "max_power": 2000,
                    "grid_connector": "GC1"
                }
            },
            "charging_stations": charging_stations
        },
        "events": events
    }

    # Write JSON
    with open(args.output, 'w') as f:
        json.dump(j, f, indent=2)
