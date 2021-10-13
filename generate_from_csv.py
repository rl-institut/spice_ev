#!/usr/bin/env python3

import argparse
import csv  # used to check if columns exists in given CSV files
import datetime
import json
from pathlib import Path  # used to check if given CSV files exist
import pandas as pd
from os import path
import numpy as np
import random

from src.util import set_options_from_config


def generate_from_csv(args):
    """Generate a scenario JSON from JSON file with LIS event data.
    args: argparse.Namespace
    """
    # missing = [arg for arg in ["input", "output"] if vars(args).get(arg) is None]
    # if missing:
    #     raise SystemExit("The following arguments are required: {}".format(", ".join(missing)))
    #
    # with open(args.input, 'r') as f:
    #     input_json = json.load(f)

    #arguments:
    interval = datetime.timedelta(minutes=args.interval)
    # read csv input file
    input = pd.read_csv("/home/inia/Dokumente/331_EBusse_Potsdam/src/data_vip/outputs/spice_ev_input/Uebersicht_SOC_PRIO_SOC_UA_SOC_PP_shift_vt.csv", index_col=0)

    input["vehicle_type"] = input["vehicle_type"] + "-" + input["charging_type"]

    count = input.groupby(by=["vehicle_type"]).size()
    vehicle_types={}
    # VEHICLES WITH THEIR CHARGING STATION
    vehicles = {}
    batteries = {}
    charging_stations = {}
    events = {
        "grid_operator_signals": [],
        "external_load": {},
        "energy_feed_in": {},
        "vehicle_events": []
    }
    for vehicle_type in input['vehicle_type'].unique():
        cp = args.capacities[vehicle_type]
        vehicle_types.update({
            vehicle_type: {
                "name": vehicle_type,
                "capacity": args.capacities[vehicle_type],
#                "mileage": None,  # kWh/100km
                "charging_curve": [[0, cp], [0.8, cp], [1, cp]],  # kW         # constant loading curve is assumed
                "min_charging_power": 0,  # kW
                "v2g": args.v2g,
                "count": count.loc[count.index == vehicle_type].values[0]
            },
        })

    number_busses_per_type = get_number_busses_per_bustype(input)

    for bus_type in number_busses_per_type.keys():
        for i in range(1, number_busses_per_type[bus_type]):

            name = bus_type
            v_name = "{}_{}".format(name, i)
            cs_name = "CS_" + v_name
            vehicles[v_name] = {                                               # Startbedingungen beim Aufsetzten des Fahrzeugs
                "connected_charging_station": None,                            # wird beim arrival event gesetzt
                "estimated_time_of_departure": None,                           # wird beim arrival event gesetzt
                "desired_soc": args.min_soc,
                "soc": 1,                                                      # aktueller SOC wird beim arrival event gesetzt
                "vehicle_type": name
            }
            t = vehicle_types[name]
            cs_power = max([v[1] for v in t['charging_curve']])                # aus Ladeleistung für die Busse
            charging_stations[cs_name] = {
                "max_power": cs_power,
                "min_power": 0.1 * cs_power,
                "parent": "GC1"
            }

            # filter all rides for that bus
            vid_list = input.loc[(input["vehicle_id"] == v_name)]
            # sort for arrival time
            vid_list = vid_list.sort_values(by = "Zeit Ende Umlauf")

            #check
            vt_counts = vid_list["Tag"].value_counts()
            if (vt_counts > 1).any():
                raise ValueError
            vid_list.reset_index(inplace=True)
            for i, row in vid_list.iterrows():
                x = 0
                arrival = vid_list["Zeit Ende Umlauf"].iloc[i]
                arrival = pd.to_datetime(arrival,
                                         format='%Y/%m/%d %H:%M:%S', utc=True)
                try:
                    departure = vid_list["Zeit Anfang Umlauf"].iloc[i+1]
                    departure = pd.to_datetime(departure,
                                           format='%Y/%m/%d %H:%M:%S', utc=True)
                    next_arrival = vid_list["Zeit Ende Umlauf"].iloc[i+1]
                    next_arrival = pd.to_datetime(next_arrival,
                                         format='%Y/%m/%d %H:%M:%S', utc=True)
                except:
                    x = 1
                    departure = arrival + pd.Timedelta(
                            8, unit='h')



                events["vehicle_events"].append({
                    "signal_time": arrival.isoformat(),
                    "start_time": arrival.isoformat(),
                    "vehicle_id": v_name,
                    "event_type": "arrival",
                    "update": {
                        "connected_charging_station": "CS_" + v_name,
                        "estimated_time_of_departure": departure.isoformat(),
                        "soc": vid_list["Ladezustand Ende in %"].iloc[i]/100,
                        "soc_delta": (100 - vid_list[
                            "Ladezustand Ende in %"].iloc[0]) / 100 * (-1)
                    }
                })
                if x == 0:
                    events["vehicle_events"].append({
                        "signal_time": departure.isoformat(),
                        "start_time": departure.isoformat(),
                        "vehicle_id": v_name,
                        "event_type": "departure",
                        "update": {
                            "estimated_time_of_arrival":  next_arrival.isoformat()
                        }
                    })

    for idx, (capacity, c_rate) in enumerate(args.battery):
        if capacity > 0:
            max_power = c_rate * capacity
        else:
            # unlimited battery: set power directly
            max_power = c_rate
        batteries["BAT{}".format(idx+1)] = {
            "parent": "GC1",
            "capacity": capacity,
            "charging_curve": [[0, max_power], [1, max_power]]
        }
    # save path and options for CSV timeseries
    # all paths are relative to output file
    target_path = path.dirname(args.output)
    input = input.sort_values(by="Zeit Ende Umlauf")
    start = input["Zeit Ende Umlauf"].iloc[0]
    start = pd.to_datetime(start, format='%Y/%m/%d %H:%M:%S', utc=True)
    stop = start + datetime.timedelta(days=args.days)
    stop = pd.to_datetime(stop, format='%Y/%m/%d %H:%M:%S', utc=True)


    if args.include_ext_load_csv:
        filename = args.include_ext_load_csv
        basename = path.splitext(path.basename(filename))[0]
        options = {
            "csv_file": filename,
            "start_time": start.astimezone().replace(microsecond=0).isoformat(),
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
            print(
                "Warning: external csv file '{}' does not exist yet".format(
                    ext_csv_path))

    if args.include_feed_in_csv:
        filename = args.include_feed_in_csv
        basename = path.splitext(path.basename(filename))[0]
        options = {
            "csv_file": filename,
            "start_time": start.astimezone().replace(microsecond=0).isoformat(),
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
            print(
                "Warning: feed-in csv file '{}' does not exist yet".format(
                    feed_in_path))

    if args.include_price_csv:
        filename = args.include_price_csv
        # basename = path.splitext(path.basename(filename))[0]
        options = {
            "csv_file": filename,
            "start_time": start.astimezone().replace(microsecond=0).isoformat(),
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
            print("Warning: price csv file '{}' does not exist yet".format(
                price_csv_path))


    daily = datetime.timedelta(days=1)
    # count number of trips where desired_soc is above min_soc
    trips_above_min_soc = 0
    # price events

    now = start - daily
    while now < stop + 2 * daily:
        now += daily
        # create vehicle events for this day
        for v_id, v in vehicles.items():
            if now.weekday() == 6:
                # no driving on Sunday
                break

            if now >= stop:
                # after end of scenario: keep generating trips, but don't include in scenario
                continue

        # generate prices for the day
        if not args.include_price_csv and now < stop:
            morning = now + datetime.timedelta(hours=6)
            evening_by_month = now + datetime.timedelta(
                hours=22 - abs(6 - now.month))
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


    j = {
        "scenario": {
            "start_time": start.isoformat(),
#             "stop_time": stop.isoformat(),                                        # entweder oder n_intervall
            "interval": interval.days * 24 * 60 + interval.seconds // 60,
            "n_intervals": (stop - start) // interval
        },
        "constants": {
            "vehicle_types": vehicle_types,
            "vehicles": vehicles,
            "grid_connectors": {
                "GC1": {
                    "max_power": vars(args).get("gc_power", 530),
                    "cost": {"type": "fixed", "value": 0.3}
                }
            },
            "charging_stations": charging_stations,
            "batteries": batteries
        },
        "events": events
    }

    # if trips_above_min_soc:
    #     print("{} trips use more than {}% capacity".format(trips_above_min_soc,
    #                                                        args.min_soc * 100))

    # Write JSON
    with open(args.output, 'w') as f:
        json.dump(j, f, indent=2,cls=NpEncoder)


def get_number_busses_per_bustype(df):

    type = {}
    for bus_type in df["vehicle_type"].unique():
#        for ct in df["charging_type"].unique():
            type[bus_type] = list()
    # sort Einfahrtzeiten
    for day in range(1, 8):
        df_day = df.loc[df["Tag"] == day]
        for bus_type in df["vehicle_type"].unique():
            type_count = df_day.loc[df_day["vehicle_type"] == bus_type][
                "Umlauf_ID"].count()
            type[bus_type].append(type_count)

    for bus_type in type.keys():
        type[bus_type] = max(type[bus_type])
    return type



class NpEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super(NpEncoder, self).default(obj)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Generate scenarios as JSON files for vehicle charging modelling')
    parser.add_argument('output', nargs='?', help='output file name (example.json)')
    parser.add_argument('--capacities', metavar=('N', 'TYPE'), nargs=2, action='append', type=str,
                        help='set number of cars for a vehicle type, \
                        e.g. `--cars 100 sprinter` or `--cars 13 golf`')
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
    parser.add_argument('--config', help='Use config file to set arguments', default='examples/generate_from_csv.cfg')
#    arser.add_argument('--sum', dest='accumulate', action='store_const',
#                       const=sum,
#                       help='sum the integers (default: find the max)')

    args = parser.parse_args()

    set_options_from_config(args, check=False, verbose=False)

    generate_from_csv(args)
