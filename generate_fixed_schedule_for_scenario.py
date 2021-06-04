#!/usr/bin/env python3

import argparse
import datetime
import json
import os

import pandas as pd

from src.scenario import Scenario
from src.scheduler import Scheduler


def main():
    parser = argparse.ArgumentParser(
        description='Generate energy price as CSV. \
        These files can be included when generating JSON files.')
    parser.add_argument('--input', help='input file name of time series(nsm_example.csv)')
    parser.add_argument('--output', help='output file name of schedule(nsm_example.csv)')
    parser.add_argument('--scenario', help='scenario file name (example.json)')
    parser.add_argument('--max_load_range', default=0.1, help='Area around max_load that should be discouraged')
    parser.add_argument('--year', default=2020, help='Year of the final schedule (not of the input)')
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
    df = pd.read_csv(args.input, index_col=0, parse_dates=True)

    # Change year to chosen year, since we only have data for 2018
    df.index = [dt.replace(year=args.year) for dt in df.index]

    scheduler = Scheduler(df, args.max_load_range, max_load)

    # Get the dates in the dataframe and calculate the schedule for the whole range
    dates = df.index.map(lambda t: t.date()).unique()
    for date in dates:
        scheduler.add_flexibility_for_date_and_vehicle_groups(date, vehicle_groups)

    # Save schedule to chosen destination
    scheduler.save_schedule(args.output)

    # Update scenario with schedule info
    start_time = df.index.min() - datetime.timedelta(days=1)
    start_time = start_time.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=2)))
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
