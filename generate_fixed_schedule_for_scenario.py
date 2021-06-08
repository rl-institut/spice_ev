#!/usr/bin/env python3

import argparse
import csv
import datetime
import json
import os

from src.scenario import Scenario
from src.scheduler import Scheduler

TIMEZONE = datetime.timezone(datetime.timedelta(hours=2))


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


def main():
    parser = argparse.ArgumentParser(
        description='Generate energy price as CSV. \
        These files can be included when generating JSON files.')
    parser.add_argument('--input', help='input file name of time series(nsm_example.csv)')
    parser.add_argument('--output', help='output file name of schedule(nsm_example.csv)')
    parser.add_argument('--scenario', help='scenario file name (example.json)')
    parser.add_argument('--max_load_range', default=0.1,
                        help='Area around max_load that should be discouraged')
    parser.add_argument('--year', default=2020,
                        help='Year of the final schedule (not of the input)')
    parser.add_argument('--flexibility_per_car', default=16, help='Flexibility of each car in kWh')
    parser.add_argument('--start_time', default='20:00:00', help='Start time of flexibility window')
    parser.add_argument('--end_time', default='05:45:00', help='End time of flexibility window')

    args = parser.parse_args()

    # vehicle groups with same standing time containing:
    # (flexibility (kWh), min load (kW), start_datetime, end_datetime)
    # filled by scenario
    # For the fixed case there is only one vehicle group
    vehicle_groups = []
    with open(args.scenario, 'r') as f:
        scenario_json = json.load(f)
        scenario = Scenario(scenario_json, os.path.dirname(args.scenario))
        num_cars, max_load_cars, max_load_grid = get_fleet_info_from_scenario(scenario)
        flexibility = num_cars * args.flexibility_per_car
        min_load = (6 * 230 * num_cars) / 1000
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
            .replace(year=args.year) \
            .replace(tzinfo=TIMEZONE)
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

    scheduler = Scheduler(time_series)

    start_time = datetime.time.fromisoformat(args.start_time).replace(tzinfo=TIMEZONE)
    end_time = datetime.time.fromisoformat(args.end_time).replace(tzinfo=TIMEZONE)
    for date in unique_dates:
        datetime_from = datetime.datetime.combine(date, start_time)
        datetime_until = datetime.datetime.combine(date + datetime.timedelta(days=1), end_time)
        scheduler.add_flexibility_for_date_and_vehicle_groups(datetime_from,
                                                              datetime_until,
                                                              vehicle_groups)

    # Add percentage signal
    scheduler.add_percentage_signal(max_load)

    # Save schedule to chosen destination
    scheduler.save_schedule(args.output)

    # Update scenario with schedule info
    start_time = scheduler.time_series[0]['timestamp'] - datetime.timedelta(days=1)
    scenario_json['events']['schedule_from_csv'] = {
        'column': 'signal_kw',
        'start_time': start_time.isoformat(),
        'step_duration_s': 900,
        'csv_file': args.output,
        'grid_connector_id': 'GC_1'
    }

    # Write updated scenario into scenario file
    with open(args.scenario, 'w') as f:
        json.dump(scenario_json, f, indent=2)


def get_fleet_info_from_scenario(scenario: Scenario):
    num_cars = 0
    max_load_cars = 0
    # Get Vehicle information from scenario
    for name, vehicle in scenario.constants.vehicles.items():
        num_cars += 1
        max_load_cars += vehicle.vehicle_type.charging_curve.max_power

    # Get Grid information from scenario
    max_load_grid = 0
    for name, grid_connector in scenario.constants.grid_connectors.items():
        max_load_grid += grid_connector.max_power
    return num_cars, max_load_cars, max_load_grid


if __name__ == '__main__':
    main()
