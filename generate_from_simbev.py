#!/usr/bin/env python3

import argparse
import csv
import datetime
from functools import reduce
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
    parser.add_argument('--price-seed', '-s', type=int, default=0, help='set seed when generating energy market prices. Negative values for fixed price in cents')
    #parser.add_argument('--include_csv', nargs='*', help='include CSV for external load. You may define custom options in the form option=value')
    #parser.add_argument('--external_csv', nargs='?', help='generate CSV for external load. Not implemented.')
    args = parser.parse_args()

    # first monday of 2021
    # SimBEV uses MiD data and creates data for an exemplary week, so there are no exact dates.
    start = datetime.datetime(year=2021, month=1, day=4, tzinfo=datetime.timezone(datetime.timedelta(hours=1)))
    interval = datetime.timedelta(minutes=args.interval)
    n_intervals = 0
    price_stable_hours = 6 # for random price: remains stable for X hours
    price_interval = datetime.timedelta(hours=price_stable_hours) / interval # every X timesteps, generate new price signal

    # possible vehicle types
    vehicle_types = {
        "bev_luxury": {
            "name": "bev_luxury",
            "capacity": 120, # kWh
            "mileage": 40, # kWh / 100km
            "charging_curve": [[0, 300], [80, 300], [100, 300]], # SOC -> kWh
            "min_charging_power": 0,
        },
        "bev_medium": {
            "name": "bev_medium",
            "capacity": 100, # kWh
            "mileage": 40, # kWh / 100km
            "charging_curve": [[0, 150], [80, 150], [100, 150]], # SOC -> kWh
            "min_charging_power": 0,
        },
        "bev_mini": {
            "name": "bev_mini",
            "capacity": 70, # kWh
            "mileage": 40, # kWh / 100km
            "charging_curve": [[0, 50], [80, 50], [100, 50]], # SOC -> kWh
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

    def datetime_from_timestep(timestep):
        assert type(timestep) == int
        return start + (interval * timestep)

    # CSV files
    pathlist = list(Path(args.simbev).rglob('*.csv'))
    pathlist.sort()

    vehicles = {}
    charging_stations = {}
    events = {
        "grid_operator_signals": [],
        "external_load": {},
        "vehicle_events": []
    }

    if args.price_seed < 0:
        # use fixed price
        events["grid_operator_signals"].append({
            "signal_time": start.isoformat(),
            "grid_connector_id": "GC1",
            "start_time": start.isoformat(),
            "cost": {
                "type": "fixed",
                "value": -args.price_seed/100
            }
        })
    else:
        random.seed(args.price_seed)

    for csv_path in pathlist:
        vehicle_name = str(csv_path.stem)[:-4]
        with open(csv_path, newline='') as csvfile:
            reader = csv.DictReader(csvfile)
            for idx,row in enumerate(reader):
                if vehicle_name not in vehicles:
                    # set vehicle info from first data row
                    v_type = row["car_type"]
                    assert v_type in vehicle_types, "Unknown vehicle type for {}: {}".format(vehicle_name, v_type)
                    vehicles[vehicle_name] = {
                        "connected_charging_station": None,
                        "soc": float(row["SoC_end"]) * 100,
                        "vehicle_type": v_type
                    }
                    # set initial charge
                    vehicle_soc = float(row["SoC_end"])
                    # vehicle not actually charged in first row, so skip rest
                    continue

                # charge at this location?
                location = row["location"]
                capacity = float(row["netto_charging_capacity"])
                demand   = float(row["chargingdemand"])
                # demand only makes sense if charging station (capacity) is present
                assert capacity > 0 or demand == 0, "Charging event without charging station: {} @ row {}".format(vehicle_name, idx + 2)

                if demand > 0:
                    # set up charging point at location
                    cs_name = "{}_{}".format(vehicle_name, location.split('_')[-1])
                    if cs_name in charging_stations and charging_stations[cs_name]["max_power"] != capacity:
                        # same location type, different capacity: build new CS
                        cs_name = "{}_{}".format(cs_name, idx)
                    if cs_name not in charging_stations:
                        charging_stations[cs_name] = {
                            # get max power from charging curve
                            "max_power": capacity,
                            "parent": "GC1",
                            "min_power": capacity * 0.1,
                        }

                    # create vehicle events
                    soc_start = float(row["SoC_start"])
                    soc_end = float(row["SoC_end"])
                    park_start = int(row["park_start"])
                    park_end = int(row["park_end"])
                    event_start = datetime_from_timestep(int(row['park_start']))
                    event_end = datetime_from_timestep(int(row['park_end']))

                    assert soc_start > 0, "SoC_start must be positive, is {} for {} @ row {}".format(soc_start, vehicle_name, idx + 2)
                    assert soc_end > 0, "SoC_end must be positive, is {} for {} @ row {}".format(soc_end, vehicle_name, idx + 2)

                    # get SOC delta since last charging (should be negative)
                    drive_soc_delta = soc_start - vehicle_soc
                    # set new vehicle SOC after charging
                    vehicle_soc = soc_end

                    events["vehicle_events"].append({
                        "signal_time": event_start.isoformat(),
                        "start_time": event_start.isoformat(),
                        "vehicle_id": vehicle_name,
                        "event_type": "arrival",
                        "update": {
                            "connected_charging_station": cs_name,
                            "estimated_time_of_departure": event_end.isoformat(),
                            "desired_soc": soc_end * 100,
                            "soc_delta": drive_soc_delta * 100
                        }
                    })

                    events["vehicle_events"].append({
                        "signal_time": event_end.isoformat(),
                        "start_time": event_end.isoformat(),
                        "vehicle_id": vehicle_name,
                        "event_type": "departure",
                        "update": {
                            "estimated_time_of_arrival": None
                        }
                    })

                    n_intervals = max(n_intervals, park_end)

                    # random price: each price interval, generate new price

                    while args.price_seed >= 0 and n_intervals >= price_interval * len(events["grid_operator_signals"]):
                        price_update_idx = int(len(events["grid_operator_signals"]) * price_interval)
                        start_time = datetime_from_timestep(price_update_idx)
                        signal_time = max(start, start_time - datetime.timedelta(days=1))
                        if 6 < start_time.hour < 18:
                            # daytime: ~15ct
                            events['grid_operator_signals'].append({
                                "signal_time": signal_time.isoformat(),
                                "grid_connector_id": "GC1",
                                "start_time": start_time.isoformat(),
                                "cost": {
                                    "type": "fixed",
                                    "value": 0.15 + random.gauss(0, 0.05)
                                }
                            })
                        else:
                            # nighttime: ~5ct
                            events['grid_operator_signals'].append({
                                "signal_time": signal_time.isoformat(),
                                "grid_connector_id": "GC1",
                                "start_time": start_time.isoformat(),
                                "cost": {
                                    "type": "fixed",
                                    "value": 0.15 + random.gauss(0, 0.05)
                                }
                            })

    j = {
        "scenario": {
            "start_time": start.isoformat(),
            "interval": args.interval,
            "n_intervals": n_intervals,
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
