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


    def run(self, strategy_name, visual):
        strat = strategy.class_from_str(strategy_name)(self.constants, self.start_time, self.interval)

        event_steps = self.events.get_event_steps(self.start_time, self.n_intervals, self.interval)

        costs = []
        results = []
        external_loads = []

        for step_i in range(self.n_intervals):
            # print('step {}: {}'.format(step_i, current_time))
            res = strat.step(event_steps[step_i])
            gcs = strat.world_state.grid_connectors.values()
            # get external loads (all current loads without charging stations)
            external_loads.append(sum([gc.get_external_load(self.constants.charging_stations.keys()) for gc in gcs]))
            results.append(res)

            # get costs
            cost = 0
            for gc in gcs:
                cost += util.get_cost(sum(gc.current_loads.values()), gc.cost)
                costs.append(cost)

        print("Costs:", int(sum(costs)))

        if visual:
            print('Done. Create plots...')
            charging_stations = {}
            socs = {}
            sum_cs = {}

            # find all charging stations and vehicles in results
            for r in results:
                for cs_id in r['commands'].keys():
                    if cs_id not in charging_stations:
                        charging_stations[cs_id] = {'x': [], 'y': []}
                for v_id in r['socs'].keys():
                    if v_id not in socs:
                        socs[v_id] = {'x': [], 'y': []}
                sum_cs[r['current_time']] = 0.0

            # find in result or NULL value for each timestep
            for r in results:
                time = r['current_time']
                for cs_id in charging_stations.keys():
                    charging_stations[cs_id]['x'].append(time)
                    if cs_id in r['commands']:
                        charging_stations[cs_id]['y'].append(r['commands'][cs_id])
                        sum_cs[time] += r['commands'][cs_id]
                    else:
                        charging_stations[cs_id]['y'].append(0.0)

                for vehicle_id, soc in r['socs'].items():
                    for vehicle_id in socs.keys():
                        socs[vehicle_id]['x'].append(time)
                        if vehicle_id in r['socs']:
                            socs[vehicle_id]['y'].append(r['socs'][vehicle_id])
                        else:
                            socs[vehicle_id]['y'].append(math.nan)

            # plot!

            fig, (ax1, ax2, ax3) = plt.subplots(3, 1)
            # plt.xticks
            fig.suptitle('Strategy: {}'.format(strategy_name), fontweight='bold')

            for name, values in sorted(charging_stations.items()):
                _, = ax1.step(values['x'], values['y'], label=name)
            ax1.set_title('Charging Stations')
            ax1.set(ylabel='Power in kW')
            if len(charging_stations) <= 10:
                ax1.legend()

            for name, values in sorted(socs.items()):
                _, = ax2.step(values['x'], values['y'], label=name)
            ax2.set_title('Vehicles')
            ax2.set(ylabel='SOC in %')
            if len(charging_stations) <= 10:
                ax2.legend()

            ax3.plot(list(sum_cs.keys()), list(sum_cs.values()), label="CS")
            ax3.plot(list(sum_cs.keys()), external_loads, label="external")
            total_power = list([external_loads[i] + cs_power for i, cs_power in enumerate(sum_cs.values())])
            ax3.plot(list(sum_cs.keys()), total_power, label="total")
            ax3.set_title('Cumulative Power')
            ax3.set(ylabel='Power in kW')
            ax3.legend()
            ax3.xaxis_date() # xaxis are datetime objects
            # fig.autofmt_xdate() # rotate xaxis labels (dates) to fit

            plt.show()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Netz_eLOG modelling')
    parser.add_argument('file', nargs='?', default='tests/test_scenario.json', help='scenario JSON file')
    parser.add_argument('--strategy', '-s', nargs='?', type=str.lower, default='greedy', choices=['greedy', 'parity', 'balanced', 'foresight', 'genetic', 'inverse'],
        help='specify strategy for simulation')
    parser.add_argument('--visual', '-v', action='store_true', help='show plots')
    args = parser.parse_args()

    if args.visual:
        import matplotlib.pyplot as plt

    # Read JSON
    with open(args.file, 'r') as f:
        s = Scenario(json.load(f), os.path.dirname(args.file))

    # RUN!
    s.run(args.strategy, args.visual)
