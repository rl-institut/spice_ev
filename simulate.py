#!/usr/bin/env python3

import argparse
import datetime
import json
import math
import os

from netz_elog.scenario import Scenario


if __name__ == '__main__':

    strategies = ['greedy', 'parity', 'balanced', 'foresight', 'genetic', 'inverse', 'v2g']

    parser = argparse.ArgumentParser(description='Netz_eLOG modelling tool. Generate scenarios and simulate different charging strategies.')
    parser.add_argument('file', nargs='?', default='tests/test_scenario.json', help='Set the scenario JSON file')
    parser.add_argument('--strategy', '-s', nargs='*', default=['greedy'], help='Specify the charging strategy. '
        'One of {}'.format(', '.join(strategies)))
    parser.add_argument('--visual', '-v', action='store_true', help='Show plots of the results')
    args = parser.parse_args()

    options = {'visual': args.visual}

    # parse strategy options
    if args.strategy:
        # first argument: strategy name
        strategy_name = args.strategy.pop(0)
        if strategy_name not in strategies:
            raise NotImplementedError("Unknown strategy: {}".format(strategy_name))
        for opt_string in args.strategy:
            try:
                # key=value
                opt_key, opt_val = opt_string.split('=')
            except ValueError:
                print("Ignored option {}. Need options in the form key=value".format(opt_string))
            try:
                # option may be number
                opt_val = float(opt_val)
            except ValueError:
                # or not
                pass
            options[opt_key] = opt_val

    # Read JSON
    with open(args.file, 'r') as f:
        s = Scenario(json.load(f), os.path.dirname(args.file))

    # RUN!
    s.run(strategy_name, options)
