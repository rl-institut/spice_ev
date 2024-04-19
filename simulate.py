#!/usr/bin/env python3

import argparse
import json
from pathlib import Path
import warnings

from spice_ev.costs import calculate_costs
from spice_ev.scenario import Scenario
from spice_ev.strategy import STRATEGIES
from spice_ev.util import set_options_from_config


def simulate(args):
    """ Read simulation input arguments, set up scenario and run the simulation.

    :param args: input arguments from simulate.cfg file or command line arguments
    :type args: argparse.Namespace or dictionary
    :raises SystemExit: if required argument *input* is missing
    :raises NotImplementedError: if unknown strategy is given
    """

    if type(args) is argparse.Namespace:
        # cast arguments to dictionary for default handling
        args = vars(args)

    try:
        input_file = Path(args["input"])
        assert input_file.exists()
    except (TypeError, AssertionError):
        raise SystemExit("Please specify a valid input file.")

    options = {
        'cost_calculation': args.get("cost_calc"),
        'margin': args.get("margin"),
        'save_soc': args.get("save_soc"),
        'save_plots': args.get("save_plots"),
        'save_results': args.get("save_results"),
        'save_timeseries': args.get("save_timeseries"),
        'testing': args.get("testing"),
        'timing': args.get("eta"),
        'visual': args.get("visual"),
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
    with input_file.open('r') as f:
        s = Scenario(json.load(f), input_file.parent)

    # RUN!
    s.run(strategy_name, options)

    if args.get("cost_calc"):
        # cost calculation following directly after simulation
        for gcID, gc in s.components.grid_connectors.items():
            pv = sum([pv.nominal_power for pv in s.components.photovoltaics.values()
                      if pv.parent == gcID])
            timeseries = vars(s).get(f"{gcID}_timeseries")

            # original fixed load
            power_fix_load_list = timeseries.get("fixed load [kW]", [0] * s.n_intervals)
            # calculate grid supply for fixed load: subtract support power (where negative)
            for ts_name in ["local generation [kW]", "battery power [kW]", "sum CS power [kW]"]:
                support = timeseries.get(ts_name)
                if support is None:
                    continue
                assert len(support) == len(power_fix_load_list) == s.n_intervals
                for i in range(s.n_intervals):
                    power_fix_load_list[i] += min(support[i], 0)
            # grid supply for fixed loads can not be negative
            for i in range(s.n_intervals):
                power_fix_load_list[i] = max(power_fix_load_list[i], 0)

            # Calculate costs
            costs = calculate_costs(
                strategy=strategy_name,
                voltage_level=gc.voltage_level,
                interval=s.interval,
                timestamps_list=timeseries.get("time"),
                power_grid_supply_list=timeseries.get("grid supply [kW]"),
                price_list=timeseries.get("price [EUR/kWh]"),
                power_fix_load_list=power_fix_load_list,
                power_generation_feed_in_list=timeseries.get("generation feed-in [kW]"),
                power_v2g_feed_in_list=timeseries.get("V2G feed-in [kW]"),
                power_battery_feed_in_list=timeseries.get("battery feed-in [kW]"),
                charging_signal_list=timeseries.get("window signal [-]"),
                price_sheet_path=args.get("cost_parameters_file"),
                grid_operator=gc.grid_operator,
                results_json=args.get("save_results"),
                power_pv_nominal=pv,
                power_schedule_list=timeseries.get("schedule [kW]"),
            )
            print(f"Costs at {gcID}: {costs['total_costs_per_year']} €/a")


if __name__ == "__main__":

    parser = argparse.ArgumentParser(
        description='SpiceEV - \
        Simulation Program for Individual Charging Events of Electric Vehicles. \
        Simulate different charging strategies for a given scenario.')
    parser.add_argument('input', nargs='?', help='Set the scenario JSON file')
    parser.add_argument('--strategy', '-s', default='greedy', choices=STRATEGIES,
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
    parser.add_argument('--cost-parameters-file', '-cp', help='Get cost parameters from json file.')
    parser.add_argument('--visual', '-v', action='store_true', help='Show plots of the results')
    parser.add_argument('--eta', action='store_true',
                        help='Show estimated time to finish simulation after each step, \
                        instead of progress bar. Not recommended for fast computations.')
    parser.add_argument('--output', '-o', help='Deprecated, use save-timeseries instead')
    parser.add_argument('--save-timeseries', help='Write timesteps to file')
    parser.add_argument('--save-results', help='Write general info to file')
    parser.add_argument('--save-plots', help="Save plots (one for each GC)")
    parser.add_argument('--save-soc', help='Write SoCs of vehicles to file')
    parser.add_argument('--testing', help='Stores testing results', action='store_true')
    parser.add_argument('--config', help='Use config file to set arguments')
    args = parser.parse_args()

    set_options_from_config(args, check=parser, verbose=False)

    if args.output:
        warnings.warn("output argument is deprecated, use save-timeseries instead",
                      DeprecationWarning)
        args.save_timeseries = args.save_timeseries or args.output

    simulate(args)
