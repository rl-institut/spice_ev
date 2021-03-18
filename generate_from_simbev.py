#!/usr/bin/env python3

import argparse
import csv
import datetime
import json
import math
from pathlib import Path
import random

from netz_elog.util import datetime_from_isoformat


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Generate scenarios as JSON files for Netz_eLOG modelling')
    parser.add_argument('output', help='output file name (example.json)')
    parser.add_argument('--simbev', metavar='DIR', type=str, required=True, help='set directory with SimBEV files')
    parser.add_argument('--interval', metavar='MIN', type=int, default=15, help='set number of minutes for each timestep (Δt)')
    parser.add_argument('--days', metavar='N', type=int, default=7, help='set duration of simulation')
    parser.add_argument('--desired-soc', metavar='SOC', type=int, default=80, help='set desired SOC (0%% - 100%%) for each charging process')
    #parser.add_argument('--include_csv', nargs='*', help='include CSV for external load. You may define custom options in the form option=value')
    #parser.add_argument('--external_csv', nargs='?', help='generate CSV for external load. Not implemented.')
    args = parser.parse_args()

    # first monday of 2021
    # SimBEV uses MiD data and creates data for an exemplary week, so there are no exact dates.
    start = datetime.datetime(year=2021, month=1, day=4, tzinfo=datetime.timezone(datetime.timedelta(hours=1)))
    stop = start + datetime.timedelta(days=args.days)
    interval = datetime.timedelta(minutes=args.interval)

    # possible vehicle types
    vehicle_types = {
        "bev_luxury": {
            "name": "bev_luxury",
            "capacity": 120, # kWh
            "mileage": 40, # kWh / 100km
            "charging_curve": [[0, 22], [80, 22], [100, 0]], # SOC -> kWh
            "min_charging_power": 0,
        },
        "bev_medium": {
            "name": "bev_medium",
            "capacity": 100, # kWh
            "mileage": 40, # kWh / 100km
            "charging_curve": [[0, 22], [80, 22], [100, 0]], # SOC -> kWh
            "min_charging_power": 0,
        },
        "bev_mini": {
            "name": "bev_mini",
            "capacity": 70, # kWh
            "mileage": 40, # kWh / 100km
            "charging_curve": [[0, 22], [80, 22], [100, 0]], # SOC -> kWh
            "min_charging_power": 0,
        },
        "phev_luxury": {
            "name": "phev_luxury",
            "capacity": 40, # kWh
            "mileage": 40, # kWh / 100km
            "charging_curve": [[0, 22], [80, 22], [100, 0]], # SOC -> kWh
            "min_charging_power": 0,
        },
        "phev_medium": {
            "name": "phev_medium",
            "capacity": 100, # kWh
            "mileage": 30, # kWh / 100km
            "charging_curve": [[0, 22], [80, 22], [100, 0]], # SOC -> kWh
            "min_charging_power": 0,
        },
        "phev_mini": {
            "name": "phev_mini",
            "capacity": 70, # kWh
            "mileage": 25, # kWh / 100km
            "charging_curve": [[0, 22], [80, 22], [100, 0]], # SOC -> kWh
            "min_charging_power": 0,
        },
    }

    # Infer vehicle type from file name
    def vehicle_type_from_path(path):
        for t in vehicle_types.keys():
            if t in str(path.stem).lower():
                return t

    # Create vehicle name from file name
    def vehicle_name_from_path(path):
        return str(path.stem)[:-4]


    def datetime_from_timestep(timestep):
        assert type(timestep) == int
        return start + (interval * timestep)


    # CSV files
    pathlist = list(Path(args.simbev).rglob('*.csv'))
    pathlist.sort()

    # VEHICLES WITH THEIR CHARGING STATION
    # create vehicles and their charging stations
    vehicles = {}
    charging_stations = {}
    for csv_path in pathlist:
        v_type = vehicle_type_from_path(csv_path)
        v_name = vehicle_name_from_path(csv_path)
        cs_name = "CS_" + v_name
        soc = 100
        vehicles[v_name] = {
            "connected_charging_station": None,
            "soc": soc,
            "vehicle_type": v_type
        }

        charging_stations[cs_name] = {
            "max_power": max([v[1] for v in vehicle_types[v_type]['charging_curve']]),
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
        ],
        "external_load": {},
        "vehicle_events": []
    }

    for csv_path in pathlist:
        with open(csv_path, newline='') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                if row['location'] not in ['0_work', '6_home']:
                    continue

                charging_demand = float(row['chargingdemand'])
                if charging_demand <= 0.0:
                    continue

                v_name = vehicle_name_from_path(csv_path)
                v_type = vehicle_type_from_path(csv_path)
                cs_name = "CS_" + v_name

                event_start = datetime_from_timestep(int(row['charge_start']))
                event_end = datetime_from_timestep(int(row['charge_end']))

                soc_delta = -100.0 * charging_demand / vehicle_types[v_type]['capacity']

                events["vehicle_events"].append({
                    "signal_time": event_start.isoformat(),
                    "start_time": event_start.isoformat(),
                    "vehicle_id": v_name,
                    "event_type": "arrival",
                    "update": {
                        "connected_charging_station": cs_name,
                        "estimated_time_of_departure": event_end.isoformat(),
                        "desired_soc": args.desired_soc,
                        "soc_delta": soc_delta
                    }
                })

                events["vehicle_events"].append({
                    "signal_time": event_end.isoformat(),
                    "start_time": event_end.isoformat(),
                    "vehicle_id": v_name,
                    "event_type": "departure",
                    "update": {
                        "estimated_time_of_arrival": None
                    }
                })

    j = {
        "scenario": {
            "start_time": start.isoformat(),
            "interval": args.interval,
            "n_intervals": math.ceil((stop - start) / interval),
        },
        "constants": {
            "vehicle_types": vehicle_types,
            "vehicles": vehicles,
            "grid_connectors": {
                "GC1": {
                  "max_power": 10000
                }
            },
            "charging_stations": charging_stations
        },
        "events": events
    }

    # Write JSON
    with open(args.output, 'w') as f:
        json.dump(j, f, indent=2)
