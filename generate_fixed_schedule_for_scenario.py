#!/usr/bin/env python3

import argparse
import csv
import datetime
import json
import math
import os

from src.scenario import Scenario
from src.util import set_options_from_config

TIMEZONE = datetime.timezone(datetime.timedelta(hours=2))


def main():
    parser = argparse.ArgumentParser(
        description='Generate a schedule for a scenario.')
    parser.add_argument('--input', help='input file name of time series(nsm_example.csv)')
    parser.add_argument('--output', help='output file name of schedule(nsm_example.csv)')
    parser.add_argument('--scenario', help='scenario file name (example.json)')
    parser.add_argument('--max_load_range', default=0.1,
                        help='Area around max_load that should be discouraged')
    parser.add_argument('--flexibility_per_car', default=16, help='Flexibility of each car in kWh')
    parser.add_argument('--start_time', default='20:00:00', help='Start time of flexibility window')
    parser.add_argument('--end_time', default='05:45:00', help='End time of flexibility window')
    parser.add_argument('--config', help='Use config file to set arguments')

    args = parser.parse_args()
    set_options_from_config(args, check=True, verbose=False)

    missing = [arg for arg in ["scenario", "input", "output"] if vars(args).get(arg) is None]
    if missing:
        raise SystemExit("The following arguments are required: {}".format(", ".join(missing)))
    # vehicle groups with same standing time containing:
    # (flexibility (kWh), min load (kW), start_datetime, end_datetime)
    # filled by scenario
    # For the fixed case there is only one vehicle group
    vehicle_groups = []
    with open(args.scenario, 'r') as f:
        scenario_json = json.load(f)
        scenario = Scenario(scenario_json, os.path.dirname(args.scenario))
        num_cars, max_load_cars, max_load_grid, gc_name = get_fleet_info_from_scenario(scenario)
        flexibility = num_cars * args.flexibility_per_car
        min_load = (6 * 230 * num_cars) / 1000  # 6 Ampere * 230 V per car
        max_load = min(max_load_grid, max_load_cars)
        vehicle_groups.append(
            (flexibility, min_load, max_load, args.start_time, args.end_time)
        )

    # Load time series data
    csv_file = list(csv.DictReader(open(args.input)))

    loads = [float(x['brutto']) for x in csv_file]
    parsed_datetimes = set()
    unique_dates = set()
    time_series = list()
    for row in csv_file:
        if row['timestamp'] in parsed_datetimes:
            # Removes duplicates if there are any
            continue
        parsed_datetimes.add(row['timestamp'])
        # parse date and change year and timezone according to args
        row['timestamp'] = datetime.datetime.strptime(row['timestamp'], "%Y-%m-%d %H:%M:%S") \
            .replace(year=scenario.start_time.year) \
            .replace(tzinfo=TIMEZONE)
        if not (scenario.start_time <= row['timestamp'] <=
                scenario.stop_time + datetime.timedelta(days=1)):
            # Ignore rows that are not in the scenario time range
            continue
        row['iso_datetime'] = row['timestamp'].isoformat()

        # parse numbers from string
        row['abregelung'] = float(row['abregelung'])
        row['brutto'] = float(row['brutto'])

        # Add the priority
        row['priority'] = add_priority(row, max(loads), args.max_load_range)
        unique_dates.add(row['timestamp'].date())
        # Add fahrplan signal of 0 (default value)
        row['signal_kw'] = 0

        time_series.append(row)

    start_time = datetime.time.fromisoformat(args.start_time).replace(tzinfo=TIMEZONE)
    end_time = datetime.time.fromisoformat(args.end_time).replace(tzinfo=TIMEZONE)
    date = scenario.start_time.date()
    while date <= scenario.stop_time.date():
        datetime_from = datetime.datetime.combine(date, start_time)
        datetime_until = datetime.datetime.combine(date + datetime.timedelta(days=1), end_time)
        add_flexibility_for_date_and_vehicle_groups(time_series, datetime_from,
                                                    datetime_until, vehicle_groups)
        date = date + datetime.timedelta(days=1)

    # Add percentage signal
    add_percentage_signal(time_series, max_load)

    # Save schedule to chosen destination
    save_schedule(time_series, args.output)

    # Update scenario with schedule info
    start_time = scenario.start_time
    scenario_json['events']['schedule_from_csv'] = {
        'column': 'signal_kw',
        'start_time': start_time.isoformat(),
        'step_duration_s': 900,
        'csv_file': os.path.relpath(args.output, os.path.dirname(args.scenario)),
        'grid_connector_id': gc_name
    }

    # Write updated scenario into scenario file
    with open(args.scenario, 'w') as f:
        json.dump(scenario_json, f, indent=2)


def add_priority(row, max_network_load, max_load_range):
    if row['abregelung'] < 0:
        # Highest priority when eeg-plants are shut down
        return 1
    if row['brutto'] < 0:
        # second highest priority when the load is smaller than the feed in
        return 2
    if row['brutto'] > (1 - max_load_range) * max_network_load:
        # Lowest priority when the load is already high
        return 4
    # In all other cases
    return 3



def get_fleet_info_from_scenario(scenario: Scenario):
    num_cars = 0
    max_load_cars = 0
    # Get Vehicle information from scenario
    for name, vehicle in scenario.constants.vehicles.items():
        num_cars += 1
        max_load_cars += vehicle.vehicle_type.charging_curve.max_power

    # Get Grid information from scenario
    max_load_grid = 0
    gc_name = ""
    for name, grid_connector in scenario.constants.grid_connectors.items():
        max_load_grid += grid_connector.max_power
        gc_name = name
    return num_cars, max_load_cars, max_load_grid, gc_name


def get_time_series_indices_for_date_range(time_series, datetime_from, datetime_until):
    return [index for index, row in enumerate(time_series)
            if datetime_from <= row['timestamp'] <= datetime_until]


def add_value_to_column_by_indices(time_series, idx, column, value):
    for i in idx:
        time_series[i][column] += value


def spread_flexibility_on_priorities(time_series, datetime_from, datetime_until,
                                     flexibility, min_load, max_load):
    """
    :param time_series: A list of dicts including the priority and the timestamp
    :param datetime_from: datetime start of flexibility window
    :param datetime_until: datetime end of flexibility window
    :param flexibility: The flexibility in kWh
    :param min_load: The minimal load in the given flexibility window in kW
    :param max_load: The maximal load in the given flexibility window in kW
    """
    min_steps = math.ceil(4 * flexibility / max_load)
    max_steps = math.floor(4 * flexibility / min_load)
    priority = 1
    datetime_idx = get_time_series_indices_for_date_range(time_series,
                                                          datetime_from,
                                                          datetime_until)
    while flexibility > 0:
        if priority > 4:
            raise ValueError('Division of flexibility is impossible')
        idx = [i for i in datetime_idx if time_series[i]['priority'] == priority]

        steps = len(idx)
        if steps == 0:
            # No steps found. Flexibility has to be used in other criterias
            pass
        elif min_steps <= steps <= max_steps:
            # The flexibility can be divided equally on all steps without breaking min/max
            # load values
            kilowatt_per_step = 4 * flexibility / steps
            add_value_to_column_by_indices(time_series, idx, 'signal_kw', kilowatt_per_step)
            return
        elif steps > max_steps:
            # Equally dividing the flexibility would result in loads < min load
            # -> The flexibility is divided on the first x (=max_steps) matching the criteria
            kilowatt_per_step = 4 * flexibility / max_steps
            add_value_to_column_by_indices(time_series, idx[:max_steps], 'signal_kw',
                                           kilowatt_per_step)
            return
        elif steps < min_steps:
            # Equally dividing the flexibility would result in loads > max load
            # -> All matching entries receive the maximum load.
            # The remaining flexibility is further distributed
            used_flexibility = steps / 4 * max_load
            add_value_to_column_by_indices(time_series, idx, 'signal_kw', max_load)
            flexibility -= used_flexibility
        priority += 1


def add_flexibility_for_date_and_vehicle_groups(time_series,
                                                datetime_from,
                                                datetime_until,
                                                vehicle_groups):
    """
    param datetime_from: The start datetime of the flexibility window
    param datetime_until: The end datetime of the flexibility window
    param vehicle_groups: a list of flexibilities containing flexibility (kWh), min load (kW),
    start_time, end_time
    """
    for flexibility, min_load, max_load, start_time, end_time in vehicle_groups:
        spread_flexibility_on_priorities(time_series, datetime_from, datetime_until,
                                         flexibility, min_load, max_load)


def add_percentage_signal(time_series, max_load):
    for row in time_series:
        row['signal_percent'] = row['signal_kw'] / max_load


def save_schedule(time_series, filename):
    save_columns = ['iso_datetime', 'signal_kw', 'signal_percent']
    with open(filename, 'w', newline='') as csvfile:
        writer = csv.writer(csvfile, delimiter=',')
        writer.writerow(save_columns)
        for row in time_series:
            writer.writerow([
                row['iso_datetime'],
                row['signal_kw'],
                row['signal_percent']
            ])


if __name__ == '__main__':
    main()
