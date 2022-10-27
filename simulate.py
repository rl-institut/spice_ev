#!/usr/bin/env python3

import argparse
import json
import os
import warnings

from src.scenario import Scenario
from src.util import set_options_from_config
from calculate_costs import calculate_costs

STRATEGIES = [
    'greedy', 'greedy_market',
    'balanced', 'balanced_market',
    'peak_load_window', 'flex_window',
    'schedule', 'schedule_foresight',
    'distributed',
]


def simulate(args):
    """Reads in simulation input arguments, sets up scenario and runs the simulation.

    :param args: input arguments from simulate.cfg file or command line arguments
    :type args: argparse.Namespace or dictionary
    """

    if type(args) == argparse.Namespace:
        # cast arguments to dictionary for default handling
        args = vars(args)

    if args.get("input") is None or not os.path.exists(args["input"]):
        raise SystemExit("Please specify a valid input file.")

    options = {
        'timing': args.get("eta"),
        'visual': args.get("visual"),
        'margin': args.get("margin"),
        'save_timeseries': args.get("save_timeseries"),
        'save_soc': args.get("save_soc"),
        'save_results': args.get("save_results"),
        'testing': args.get("testing")
    }

    # parse strategy options
    strategy_name = args.get("strategy", "greedy")
    if strategy_name not in STRATEGIES:
        raise NotImplementedError("Unknown strategy: {}".format(strategy_name))
    if args.get("strategy_option"):
        for opt_key, opt_val in args["strategy_option"]:
            try:
                # option may be number
                opt_val = float(opt_val)
            except ValueError:
                # or not
                pass
            options[opt_key] = opt_val

    # Read JSON
    with open(args["input"], 'r') as f:
        s = Scenario(json.load(f), os.path.dirname(args["input"]))

    # RUN!
    (
        timestamps_list,
        power_grid_supply_list,
        price_list,
        power_fix_load_list,
        power_feed_in_list,
        charging_signal_list,
        results_file_content
    ) = s.run(strategy_name, options)

    # Cost calculation
    timestamps = timestamps_list
    power_grid_supply = power_grid_supply_list
    prices = price_list
    power_fix_load = power_fix_load_list
    charging_signals = charging_signal_list
    voltage_level = results_file_content['grid_connector']['voltage_level']
    interval_min = results_file_content["temporal_parameters"]["interval"]
    power_pv_nominal = results_file_content['photovoltaics']['nominal_power']
    core_standing_time_dict = results_file_content.get("core_standing_time")

    if args["cost_calc"]:
        (
            total_costs_per_year,
            commodity_costs_eur_per_year,
            capacity_costs_eur,
            power_procurement_per_year,
            levies_fees_and_taxes_per_year,
            feed_in_remuneration_per_year
        ) = calculate_costs(
            args["strategy"],
            voltage_level,
            interval_min,
            timestamps,
            power_grid_supply,
            prices,
            power_fix_load,
            power_feed_in_list,
            charging_signals,
            core_standing_time_dict,
            args["get_cost_parameters"],
            args["save_results"],
            power_pv_nominal
        )


if __name__ == "__main__":

    parser = argparse.ArgumentParser(
        description='SpiceEV - \
        Simulation Program for Individual Charging Events of Electric Vehicles. \
        Simulate different charging strategies for a given scenario.')
    parser.add_argument('input', nargs='?', help='Set the scenario JSON file')
    parser.add_argument('--strategy', '-s', default='greedy',
                        help='Specify the charging strategy. One of {}. You may define \
                        custom options with --strategy-option.'.format(', '.join(STRATEGIES)))
    parser.add_argument('--margin', '-m', metavar='X', type=float, default=0.05,
                        help=('Add margin for desired SOC [0.0 - 1.0].\
                        margin=0.05 means the simulation will not abort if vehicles \
                        reach at least 95%% of the desired SOC before leaving. \
                        margin=1 -> the simulation continues with every positive SOC value.'))
    parser.add_argument('--strategy-option', '-so', metavar=('KEY', 'VALUE'),
                        nargs=2, action='append',
                        help='Append additional options to the charging strategy.')
    parser.add_argument('--cost-calc', '-cc', action='store_true',
                        help='Calculate electricity costs')
    parser.add_argument('--get-cost-parameters', '-cp', help='Get cost parameters from json file.')
    parser.add_argument('--visual', '-v', action='store_true', help='Show plots of the results')
    parser.add_argument('--eta', action='store_true',
                        help='Show estimated time to finish simulation after each step, \
                        instead of progress bar. Not recommended for fast computations.')
    parser.add_argument('--output', '-o', help='Deprecated, use save-timeseries instead')
    parser.add_argument('--save-timeseries', help='Write timesteps to file')
    parser.add_argument('--save-results', help='Write general info to file')
    parser.add_argument('--save-soc', help='Write SoCs of vehicles to file')
    parser.add_argument('--testing', help='Stores testing results', action='store_true')
    parser.add_argument('--config', help='Use config file to set arguments')
    args = parser.parse_args()

    set_options_from_config(args, check=True, verbose=False)

    if args.output:
        warnings.warn("output argument is deprecated, use save-timeseries instead",
                      DeprecationWarning)
        args.save_timeseries = args.save_timeseries or args.output

    # Simulation
    simulate(args)
