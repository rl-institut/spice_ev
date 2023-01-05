#!/usr/bin/env python3

import argparse
from src.util import set_options_from_config
import generate_from_csv
import generate_from_simbev
import generate_from_statistics


if __name__ == '__main__':
    mode_choices = {
        "csv": generate_from_csv.generate_from_csv,
        "simbev": generate_from_simbev.generate_from_simbev,
        "statistics": generate_from_statistics.generate_from_statistics,
    }
    DEFAULT_START_TIME = "2023-01-01T01:00:00+02:00"

    parser = argparse.ArgumentParser(
        description='Generate scenarios as JSON files for vehicle charging modelling')
    # select generate mode
    parser.add_argument('mode', nargs='?',
                        choices=mode_choices.keys(), default="statistics",
                        help=f"select input type ({', '.join(mode_choices.keys())})")

    # general options
    parser.add_argument('--output', '-o', help='output file name (example.json)')
    parser.add_argument('--interval', metavar='MIN', type=int, default=15,
                        help='set number of minutes for each timestep (Δt)')
    parser.add_argument('--min-soc', metavar='SOC', type=float, default=0.8,
                        help='set minimum desired SOC (0 - 1) for each charging process')
    parser.add_argument('--battery', '-b', default=[], nargs=2, type=float, action='append',
                        help='add battery with specified capacity in kWh and C-rate \
                        (-1 for variable capacity, second argument is fixed power))')
    parser.add_argument('--gc-power', type=int, default=100, help='set power at grid connection '
                                                                  'point in kW')
    parser.add_argument('--voltage-level', '-vl', help='Choose voltage level for cost calculation')
    parser.add_argument('--pv-power', type=int, default=0, help='set nominal power for local '
                                                                'photovoltaic power plant in kWp')
    parser.add_argument('--cs-power-min', type=float, default=None,
                        help='set minimal power at charging station in kW (default: 0.1 * cs_power')
    parser.add_argument('--discharge-limit', default=0.5,
                        help='Minimum SoC to discharge to during v2g. [0-1]')
    parser.add_argument('--days', metavar='N', type=int, default=7,
                        help='set duration of scenario as number of days')  # ignored for simbev
    parser.add_argument('--seed', default=None, type=int, help='set random seed')

    # input files (CSV, JSON)
    parser.add_argument('--vehicle-types', default=None,
                        help='location of vehicle type definitions')
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
    # errors and warnings
    parser.add_argument('--verbose', '-v', action='count', default=0,
                        help='Set verbosity level. Use this multiple times for more output. '
                             'Default: only errors and important warnings, '
                             '1: additional warnings and info')
    # config
    parser.add_argument('--config', help='Use config file to set arguments')

    # csv options
    parser.add_argument('input_file', nargs='?',
                        help='input file name (rotations_example_table.csv)')

    # statistics options
    parser.add_argument('--vehicles', metavar=('N', 'TYPE'), nargs=2, action='append', type=str,
                        help='set number of vehicles for a vehicle type, \
                        e.g. `--vehicles 100 sprinter` or `--vehicles 13 golf`')
    parser.add_argument('--start-time', default=DEFAULT_START_TIME,
                        help='provide start time of simulation in ISO format '
                             'YYYY-MM-DDTHH:MM:SS+TZ:TZ. Precision is 1 second. E.g. '
                             '2023-01-01T01:00:00+02:00')
    parser.add_argument('--holidays', default=[],
                        help='provide list of specific days of no driving ISO format YYYY-MM-DD')
    parser.add_argument('--buffer', type=float, default=0.1,
                        help='set buffer on top of needed SoC for next trip')

    # simbev options
    parser.add_argument('--simbev', metavar='DIR', type=str, help='set directory with SimBEV files')
    parser.add_argument('--region', type=str, help='set name of region')
    parser.add_argument('--ignore-simbev-soc', action='store_true',
                        help='Don\'t use SoC columns from SimBEV files')
    parser.add_argument('--min-soc-threshold', type=float, default=0.05,
                        help='SoC below this threshold trigger a warning. Default: 0.05')
    parser.add_argument('--export-vehicle-id-csv', default=None,
                        help='option to export csv after assigning vehicle_id')

    args = parser.parse_args()

    set_options_from_config(args, check=True, verbose=args.verbose > 1)

    if not args.output:
        raise Exception("Output name must be given")

    # call generate function
    mode_choices[args.mode](args)
