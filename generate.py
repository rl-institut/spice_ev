#!/usr/bin/env python3

import argparse
import csv
import json
from pathlib import Path
import warnings

from spice_ev.util import set_options_from_config
from spice_ev.generate import generate_from_csv, generate_from_simbev, generate_from_statistics


MODE_CHOICES = {
    "csv": generate_from_csv.generate_from_csv,
    "simbev": generate_from_simbev.generate_from_simbev,
    "statistics": generate_from_statistics.generate_from_statistics,
}


def update_namespace(args):
    """
    Prepare generate-arguments for function call. Various checks and preparations.
    :param args: argparse arguments
    :type args: Namespace
    """
    # handle vehicle types file (except simbev, which uses metadata)
    if args.mode != "simbev":
        if args.vehicle_types is None:
            args.vehicle_types = "examples/data/vehicle_types.json"
            print(f"No definition of vehicle types found, using {args.vehicle_types}.")
        ext = args.vehicle_types.split('.')[-1]
        if ext != "json":
            warnings.warn("File extension mismatch: vehicle type file should be '.json'")
        with open(args.vehicle_types) as f:
            args.predefined_vehicle_types = json.load(f)

    # check voltage level (used in cost calculation)
    voltage_level = vars(args).get("voltage_level")
    if voltage_level is None:
        warnings.warn("Voltage level is not set, please choose one when calculating costs.")

    # prepare GC
    args.gc = {
        "GC1": {
            "max_power": vars(args).get("gc_power", 100),
            "voltage_level": voltage_level,
            "cost": {"type": "fixed", "value": 0.3},
        }
    }

    # prepare PV
    pv_power = vars(args).get("pv_power", 0)
    if pv_power:
        args.pv = {
            "PV1": {
                "parent": "GC1",
                "nominal_power": pv_power,
            }
        }
    else:
        args.pv = {}

    # prepare stationary battery
    batteries = {}
    for idx, (capacity, c_rate) in enumerate(args.battery):
        if capacity > 0:
            max_power = c_rate * capacity
        else:
            # unlimited battery: set power directly
            max_power = c_rate
        batteries["BAT{}".format(idx + 1)] = {
            "parent": "GC1",
            "capacity": capacity,
            "charging_curve": [[0, max_power], [1, max_power]]
        }
    args.battery = batteries

    # external input CSV files
    csv_files = {
        "fixed load": {
            "filename": args.include_fixed_load_csv,
            "options": "include_fixed_load_csv_option",
            "default_step_duration_s": 900,  # 15 minutes
            "default_column": "energy",
        },
        "local_generation": {
            "filename": args.include_local_generation_csv,
            "options": "include_local_generation_csv_option",
            "default_step_duration_s": 3600,  # 60 minutes
            "default_column": "energy",
        },
        "price": {
            "filename": args.include_price_csv,
            "options": "include_price_csv_option",
            "default_step_duration_s": 3600,  # 60 minutes
            "default_column": "price [ct/kWh]",
        },
    }
    # define target path for relative output files
    target_path = Path(args.output).parent

    for file_type, file_info in csv_files.items():
        if file_info["filename"]:
            options = {
                "csv_file": file_info["filename"],
                "start_time": None,
                "step_duration_s": file_info["default_step_duration_s"],
                "grid_connector_id": "GC1",
                "column": file_info["default_column"],
            }
            for key, value in vars(args)[file_info["options"]]:
                if key == "step_duration_s":
                    value = int(value)
                options[key] = value
            vars(args)[file_info["options"]] = options

            # check if CSV file exists
            ext_csv_path = target_path.joinpath(file_info["filename"])
            if not ext_csv_path.exists():
                warnings.warn(f"{file_type} csv file '{ext_csv_path}' does not exist yet.")
            else:
                # check if given column exists in file
                with open(ext_csv_path, newline='') as csvfile:
                    reader = csv.DictReader(csvfile)
                    if not options["column"] in reader.fieldnames:
                        warnings.warn(f"{file_type} csv file '{ext_csv_path} "
                                      f"has no column {options['column']}'.")


def generate(args):
    """
    Generate scenario JSON.

    Own function for testing.
    :param args: argparse arguments
    :type args: Namespace
    :raises SystemExit: if required arguments are missing
    """
    # check for necessary arguments
    required = {
        "csv": ["input_file", "output"],
        "simbev": ["simbev", "output"],
        "statistics": ["output"],
    }
    missing = [arg for arg in required[args.mode] if vars(args).get(arg) is None]
    if missing:
        raise SystemExit("The following arguments are required: {}".format(", ".join(missing)))

    update_namespace(args)

    # call generate function
    scenario = MODE_CHOICES[args.mode](args)

    # write JSON
    with open(args.output, 'w') as f:
        json.dump(scenario, f, indent=2)


if __name__ == '__main__':

    DEFAULT_START_TIME = "2023-01-01T01:00:00+02:00"

    parser = argparse.ArgumentParser(
        description='Generate scenarios as JSON files for vehicle charging modelling')
    # select generate mode
    parser.add_argument('mode', nargs='?',
                        choices=MODE_CHOICES.keys(), default="statistics",
                        help=f"select input type ({', '.join(MODE_CHOICES.keys())})")

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
    parser.add_argument('--include-fixed-load-csv',
                        help='include CSV for fixed load. \
                        You may define custom options with --include-fixed-load-csv-option')
    parser.add_argument('--include-fixed-load-csv-option', '-eo', metavar=('KEY', 'VALUE'),
                        nargs=2, default=[], action='append',
                        help='append additional argument to fixed load')
    parser.add_argument('--include-local-generation-csv',
                        help='include CSV for local energy generation, e.g., local PV. \
                        You may define custom options with --include-local-generation-csv-option')
    parser.add_argument('--include-local-generation-csv-option', '-fo', metavar=('KEY', 'VALUE'),
                        nargs=2, default=[], action='append',
                        help='append additional argument to local generation')
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
    parser.add_argument('--input-file', '-f',
                        help='input file name (rotations_example_table.csv)')
    parser.add_argument('--export-vehicle-id-csv', default=None,
                        help='option to export csv after assigning vehicle_id')

    # simbev options
    parser.add_argument('--simbev', metavar='DIR', type=str, help='set directory with SimBEV files')
    parser.add_argument('--region', type=str, help='set name of region')
    parser.add_argument('--ignore-simbev-soc', action='store_true',
                        help='Don\'t use SoC columns from SimBEV files')
    parser.add_argument('--min-soc-threshold', type=float, default=0.05,
                        help='SoC below this threshold trigger a warning. Default: 0.05')

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

    args = parser.parse_args()

    set_options_from_config(args, check=True, verbose=args.verbose > 1)

    generate(args)
