#!/usr/bin/env python3

import argparse
import datetime
import json
import math
import os

from netz_elog.scenario import Scenario


if __name__ == '__main__':

    strategies = [
        'greedy', 'greedy_feed_in',
        'parity',
        'balanced', 'balanced_feed_in',
        'foresight',
        'genetic',
        'inverse',
        'v2g'
    ]

    parser = argparse.ArgumentParser(description='Netz_eLOG modelling tool. Simulate different charging strategies for a given scenario.')
    parser.add_argument('file', help='Set the scenario JSON file')
    parser.add_argument('--strategy', '-s', default='greedy', help='Specify the charging strategy. '
        'One of {}. You may define custom options with --strategy-option.'.format(', '.join(strategies)))
    parser.add_argument('--strategy-option', '-so', metavar=('KEY', 'VALUE'), nargs=2, action='append',
        help='Append additional options to the charging strategy.')
    parser.add_argument('--visual', '-v', action='store_true', help='Show plots of the results')
    args = parser.parse_args()

    options = {'visual': args.visual}

    # parse strategy options
    if args.strategy:
        # first argument: strategy name
        strategy_name = args.strategy
        if strategy_name not in strategies:
            raise NotImplementedError("Unknown strategy: {}".format(strategy_name))
        if args.strategy_option:
            for opt_key, opt_val in args.strategy_option:
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
