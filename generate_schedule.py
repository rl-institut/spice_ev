#!/usr/bin/env python3

import argparse
import json
from json.decoder import JSONDecodeError
import warnings

from spice_ev import util
from spice_ev.generate.generate_schedule import generate_schedule


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Generate a schedule for a scenario.')
    parser.add_argument('scenario', nargs='?', help='Scenario input file')
    parser.add_argument('--input',
                        help='Timeseries with power and limit. '
                        'Columns: "curtailment", "residual load" (timestamp ignored)')
    parser.add_argument('--output', '-o',
                        help='Specify schedule file name, '
                        'defaults to <scenario>_schedule.csv')
    parser.add_argument('--individual', '-i', action='store_true',
                        help='schedule based on individual vehicles instead of vehicle park')
    parser.add_argument('--priority-percentile', default=0.25, type=float,
                        help='Percentiles for priority determination')
    parser.add_argument('--core-standing-time', default=None,
                        help='Define time frames as well as full '
                        'days during which the fleet is guaranteed to be available in a JSON '
                        'obj like: {"times":[{"start": [22,0], "end":[1,0]}], "no_drive_days":[6]}')
    parser.add_argument('--visual', '-v', action='store_true', help='Plot flexibility and schedule')
    parser.add_argument('--config', help='Use config file to set arguments')

    args = parser.parse_args()

    # parse JSON obj for core standing time if supplied via cmd line
    try:
        args.core_standing_time = json.loads(args.core_standing_time)
    except JSONDecodeError:
        args.core_standing_time = None
        warnings.warn('Value for core standing time could not be parsed and is omitted.')
    except TypeError:
        # no core standing time provided, defaulted to None
        pass

    util.set_options_from_config(args, check=parser, verbose=False)

    missing = [arg for arg in ["scenario", "input"] if vars(args).get(arg) is None]
    if missing:
        raise SystemExit("The following arguments are required: {}".format(", ".join(missing)))

    generate_schedule(args)
