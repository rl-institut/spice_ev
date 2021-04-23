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
    parser.add_argument('--price-seed', '-s', type=int, default=0, help='set seed when generating energy market prices. Negative values for fixed price in cents')
    parser.add_argument('--min-soc', type=float, default=0.5, help='Set minimum desired SoC for each charging event. Default: 0.5')
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

    SOC_THRESHOLD = 0.0 # don't charge if next trip uses less than this SOC

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

    # generate vehicle events: iterate over input files
    for csv_path in pathlist:
        # get vehicle name from file name
        vehicle_name = str(csv_path.stem)[:-4]
        with open(csv_path, newline='') as csvfile:
            reader = csv.DictReader(csvfile)
            for idx,row in enumerate(reader):
                if vehicle_name not in vehicles:
                    # set vehicle info from first data row
                    v_type = row["car_type"]
                    # vehicle type must be known
                    assert v_type in vehicle_types, "Unknown vehicle type for {}: {}".format(vehicle_name, v_type)
                    # save initial vehicle data
                    vehicles[vehicle_name] = {
                        "connected_charging_station": None,
                        "soc": float(row["SoC_end"]) * 100,
                        "vehicle_type": v_type
                    }

                    # check that capacities match
                    vehicle_capacity = vehicle_types[v_type]["capacity"]
                    file_capacity = int(row["bat_cap"])
                    assert vehicle_capacity == file_capacity, "Capacity of vehicle {} does not match (in file: {}, in script: {})".format(vehicle_name, file_capacity, vehicle_capacity)


                    # set initial charge
                    vehicle_soc = float(row["SoC_end"])
                    last_cs_event = None
                    soc_needed = 0.0
                    park_end_ts = datetime_from_timestep(int(row['park_end']) + 1)
                    # vehicle not actually charged in first row, so skip rest
                    continue

                # read info from row
                location = row["location"]
                capacity = float(row["netto_charging_capacity"])
                consumption = float(row["consumption"])

                # general sanity checks
                simbev_soc_start = float(row["SoC_start"])
                simbev_soc_end = float(row["SoC_end"])
                assert simbev_soc_start > 0 and simbev_soc_end > 0, "SimBEV created negative SoC for {} in row {}".format(vehicle_name, idx + 2)
                simbev_demand = float(row["chargingdemand"])
                assert capacity > 0 or simbev_demand == 0, "Charging event without charging station: {} @ row {}".format(vehicle_name, idx + 2)

                if not capacity or soc_needed < SOC_THRESHOLD:
                    # no charging station or don't need to charge
                    # just increase charging demand based on consumption
                    soc_needed += consumption / vehicle_capacity
                    assert soc_needed <= 1 + vehicle_soc, "Consumption too high for {} in row {}: vehicle charged to {}, needs SoC of {} ({} kW)".format(vehicle_name, idx + 2, vehicle_soc, soc_needed, soc_needed * vehicle_capacity)
                else:
                    # charging station present
                    # must have no consumption
                    assert consumption == 0, "Consumption while charging for {} @ row {}".format(vehicle_name, idx + 2)

                    if not last_cs_event:
                        # first charge: initial must be enough
                        assert vehicle_soc >= soc_needed, "Initial charge for {} is not sufficient".format(vehicle_name)
                    else:
                        # update desired SoC from last charging event
                        # this much charge must be in battery when leaving CS
                        # to reach next CS (the one from current row)
                        desired_soc = max(args.min_soc, soc_needed)

                        # this much must be charged
                        delta_soc = max(desired_soc - vehicle_soc, 0)

                        # check if charging is possible in ideal case
                        cs_name = last_cs_event["update"]["connected_charging_station"]
                        cs_power = charging_stations[cs_name]["max_power"]
                        charge_duration = park_end_ts - park_start_ts
                        possible_power = cs_power * charge_duration.seconds/3600
                        possible_soc = possible_power / vehicle_capacity

                        if delta_soc > possible_soc:
                            print("WARNING: Can't fulfill charging request for {} in ts {:.0f}. Need {:.2f} kWh in {:.2f} h from {} kW CS, possible: {} kWh".format(vehicle_name, (park_end_ts - start)/interval, desired_soc * vehicle_capacity, charge_duration.seconds/3600, cs_power, possible_power))

                        # update last charge event info: set desired SOC
                        last_cs_event["update"]["desired_soc"] = desired_soc * 100
                        events["vehicle_events"].append(last_cs_event)

                        # simulate charging
                        vehicle_soc = max(vehicle_soc, desired_soc)

                    # update vehicle SOC: with how much SOC does car arrive at new CS?
                    vehicle_soc -= soc_needed

                    # initialize new charge event

                    # setup charging point at location
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

                    # generate vehicle events
                    # departure from old CS
                    park_start_ts = datetime_from_timestep(int(row["park_start"]))
                    events["vehicle_events"].append({
                        "signal_time": park_end_ts.isoformat(),
                        "start_time": park_end_ts.isoformat(),
                        "vehicle_id": vehicle_name,
                        "event_type": "departure",
                        "update": {
                            "estimated_time_of_arrival": park_start_ts.isoformat()
                        }
                    })

                    # arrival at new CS
                    park_end_idx = int(row["park_end"]) + 1
                    park_end_ts = datetime_from_timestep(park_end_idx)
                    last_cs_event = {
                        "signal_time": park_start_ts.isoformat(),
                        "start_time": park_start_ts.isoformat(),
                        "vehicle_id": vehicle_name,
                        "event_type": "arrival",
                        "update": {
                            "connected_charging_station": cs_name,
                            "estimated_time_of_departure": park_end_ts.isoformat(),
                            "desired_soc": None, # updated later
                            "soc_delta": - soc_needed * 100
                        }
                    }

                    # reset distance (needed charge) to next CS
                    soc_needed = 0.0

                    # get maximum length of timesteps (only end of last charge relevant)
                    n_intervals = max(n_intervals, park_end_idx)

                    # random price: each price interval, generate new price

                    while args.price_seed >= 0 and n_intervals >= price_interval * len(events["grid_operator_signals"]):
                        # at which timestep is price updated?
                        price_update_idx = int(len(events["grid_operator_signals"]) * price_interval)
                        start_time = datetime_from_timestep(price_update_idx)
                        # price signal known one day ahead
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
