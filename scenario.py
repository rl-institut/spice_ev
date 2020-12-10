#!/usr/bin/env python3

import argparse
import datetime
import json
import math
import os

import constants
import events
import strategy
import util


class Scenario:
    """ A scenario
    """
    def __init__(self, json_dict, dir_path=''):
        self.constants = constants.Constants(json_dict.get('constants'))
        self.events = events.Events(json_dict.get('events'), dir_path)

        scenario = json_dict.get('scenario')

        self.start_time = util.datetime_from_isoformat(scenario['start_time'])
        self.interval =  datetime.timedelta(minutes=scenario['interval'])

        # compute n_intervals or stop_time
        assert (scenario.get('stop_time') != None) ^ (scenario.get('n_intervals') != None), 'just one of\'em plz'
        if 'n_intervals' in scenario:
            self.n_intervals = scenario['n_intervals']
            self.stop_time = self.start_time + self.interval * self.n_intervals
        else:
            stop_time = util.datetime_from_isoformat(scenario['stop_time'])
            delta = stop_time - self.start_time
            self.n_intervals = delta / self.interval

        # for gc_id, gc in self.constants.grid_connectors.items():
            # ext_lists = list(filter(lambda l: l.grid_connector_id == gc_id, self.events.external_load_lists))
            # gc.compute_avg_ext_load_week(ext_lists, self.interval)
        for ext_load_list in self.events.external_load_lists.values():
            gc_id = ext_load_list.grid_connector_id
            gc = self.constants.grid_connectors[gc_id]
            gc.add_avg_ext_load_week(ext_load_list, self.interval)


    def run(self, strategy_name, options):
        options['interval'] = self.interval
        strat = strategy.class_from_str(strategy_name)(self.constants, self.start_time, **options)

        event_steps = self.events.get_event_steps(self.start_time, self.n_intervals, self.interval)

        costs = []
        prices = []
        results = []
        ext_load = []
        predicted = []
        diffToPred = []

        for step_i in range(self.n_intervals):
            # print('step {}: {}'.format(step_i, current_time))
            res = strat.step(event_steps[step_i])
            gcs = strat.world_state.grid_connectors.values()
            # get external loads (all current loads without charging stations)
            ext_load.append(sum([gc.get_external_load(self.constants.charging_stations.keys()) for gc in gcs]))
            predicted.append(sum([gc.get_avg_ext_load(strat.current_time, self.interval) for gc in gcs]))
            diffToPred.append(ext_load[-1] - predicted[-1])
            results.append(res)

            # get prices and costs
            cost = 0
            price = []
            for gc_id, gc in sorted(strat.world_state.grid_connectors.items()):
                cost += util.get_cost(sum(gc.current_loads.values()), gc.cost)
                price.append(util.get_cost(1, gc.cost))
            costs.append(cost)
            prices.append(price)

        print("Costs:", int(sum(costs)))

        if options.get('visual', False):
            print('Done. Create plots...')

            socs  = []
            sum_cs = []
            xlabels = []

            for r in results:
                xlabels.append(r['current_time'])

                cur_car = []
                cur_cs  = []
                for v_id in sorted(self.constants.vehicles):
                    cur_car.append(r['socs'].get(v_id, None))
                socs.append(cur_car)
                for cs_id in sorted(self.constants.charging_stations):
                    cur_cs.append(r['commands'].get(cs_id, 0.0))
                sum_cs.append(cur_cs)

            # plot!
            fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2,2)
            fig.suptitle('Strategy: {}: {}€'.format(strat.description, int(sum(costs))), fontweight='bold')

            lines = ax1.step(xlabels, sum_cs)
            ax1.set_title('Charging Stations')
            ax1.set(ylabel='Power in kW')
            if len(self.constants.charging_stations) <= 10:
                ax1.legend(lines, sorted(self.constants.charging_stations.keys()))

            lines = ax2.step(xlabels, socs)
            ax2.set_title('Vehicles')
            ax2.set(ylabel='SOC in %')
            if len(self.constants.vehicles) <= 10:
                ax2.legend(lines, sorted(self.constants.vehicles.keys()))

            ax3.plot(xlabels, list([sum(cs) for cs in sum_cs]), label="CS")
            ax3.plot(xlabels, ext_load, label="external")
            total_power = list([ext_load[i] + sum(sum_cs[i]) for i in range(len(xlabels))])
            # ax3.plot(xlabels, predicted, label="Prediction")
            # ax3.plot(xlabels, diffToPred, label="Difference")
            ax3.plot(xlabels, total_power, label="total")
            # ax3.axhline(color='k', linestyle='--', linewidth=1)
            ax3.set_title('Cumulative Power')
            ax3.set(ylabel='Power in kW')
            ax3.legend()
            ax3.xaxis_date() # xaxis are datetime objects

            lines = ax4.step(xlabels, prices)
            ax4.set_title('Price for 1 kWh')
            ax4.set(ylabel='€')
            if len(self.constants.grid_connectors) <= 10:
                ax4.legend(lines, sorted(self.constants.grid_connectors.keys()))

            fig.autofmt_xdate() # rotate xaxis labels (dates) to fit
            plt.show()

if __name__ == '__main__':

    strategies = ['greedy', 'parity', 'balanced', 'foresight', 'genetic', 'inverse', 'v2g']

    parser = argparse.ArgumentParser(description='Netz_eLOG modelling')
    parser.add_argument('file', nargs='?', default='tests/test_scenario.json', help='scenario JSON file')
    parser.add_argument('--strategy', '-s', nargs='*', default=['greedy'], help='specify strategy for simulation')
    parser.add_argument('--visual', '-v', action='store_true', help='show plots')
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

    if args.visual:
        import matplotlib.pyplot as plt

    # Read JSON
    with open(args.file, 'r') as f:
        s = Scenario(json.load(f), os.path.dirname(args.file))

    # RUN!
    s.run(strategy_name, options)
