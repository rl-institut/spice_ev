#!/usr/bin/env python3

import argparse
import datetime
import json
import math
import os

from netz_elog import constants, events, strategy, util


class Scenario:
    """ A scenario
    """
    def __init__(self, json_dict, dir_path=''):
        # get constants and events
        self.constants = constants.Constants(json_dict.get('constants'))
        self.events = events.Events(json_dict.get('events'), dir_path)

        scenario = json_dict.get('scenario')

        # compute time stuff
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

        # compute average load for each timeslot
        for ext_load_list in self.events.external_load_lists.values():
            gc_id = ext_load_list.grid_connector_id
            gc = self.constants.grid_connectors[gc_id]
            gc.add_avg_ext_load_week(ext_load_list, self.interval)


    def run(self, strategy_name, options):
        # run scenario
        options['interval'] = self.interval
        strat = strategy.class_from_str(strategy_name)(self.constants, self.start_time, **options)

        event_steps = self.events.get_event_steps(self.start_time, self.n_intervals, self.interval)

        costs = []
        prices = []
        results = []
        extLoads = []
        totalLoad = []
        totalFeedIn = 0
        unusedFeedIn = 0
        batteryLevels = {k: [] for k in self.constants.batteries.keys()}


        for step_i in range(self.n_intervals):
            # run single timestep
            res = strat.step(event_steps[step_i])
            gcs = strat.world_state.grid_connectors.values()

            # get current loads
            cost = 0
            price = []
            curLoad = 0
            for gc in gcs:
                # loads without charging stations (external + feed-in)
                stepLoads = {k: v for k,v in gc.current_loads.items() if k not in self.constants.charging_stations.keys()}
                extLoads.append(stepLoads)
                # sum up loads (with charging stations), compute cost
                gc_load = gc.get_current_load()
                cost += util.get_cost(max(gc_load, 0), gc.cost)
                price.append(util.get_cost(1, gc.cost))
                curLoad += gc_load

                # sum up total feed-in power
                feed_in_keys = self.events.energy_feed_in_lists.keys()
                totalFeedIn -= sum([gc.current_loads.get(k, 0) for k in feed_in_keys])
                # sum up unused feed-in power (negative total power)
                unusedFeedIn -= min(gc.get_current_load(), 0)

            costs.append(cost)
            prices.append(price)
            totalLoad.append(max(curLoad, 0))

            results.append(res)

            for batName, bat in strat.world_state.batteries.items():
                batteryLevels[batName].append(bat.soc / 100 * bat.capacity)

        print("Costs:", int(sum(costs)))
        print("Renewable energy feed-in: {} kW, unused: {} kW ({}%)".format(
            round(totalFeedIn),
            round(unusedFeedIn),
            round((unusedFeedIn)*100/totalFeedIn) if totalFeedIn > 0 else 0)
        )
        for batName, values in batteryLevels.items():
            print("Maximum stored power for {}: {:.2f} kW".format(batName, max(values)))

        if options.get('output', None):
            cs_ids = strat.world_state.charging_stations.keys()
            with open(options['output'], 'w') as output_file:
                # write header
                header = ["timestep", "time"]
                header += [cs_id for cs_id in cs_ids]
                output_file.write(','.join(header) + '\n')

                # write timesteps
                for idx, r in enumerate(results):
                    time = r['current_time']
                    row = [str(idx), str(time)]
                    row += [str(r['commands'].get(cs_id, 0)) for cs_id in cs_ids]
                    output_file.write(','.join(row) + '\n')

        if options.get('visual', False):
            import matplotlib.pyplot as plt

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

            # untangle external loads (with feed-in)
            loads = {}
            for i, step in enumerate(extLoads):
                currentLoad = 0
                for k, v in step.items():
                    if k not in loads:
                        # new key, not present before
                        loads[k] = [0] * i
                    loads[k].append(v)
                for k in loads.keys():
                    if k not in step:
                        # old key not in current step
                        loads[k].append(0)

            # plot!

            # batteries
            if batteryLevels:
                plots_top_row = 3
                ax = plt.subplot(2, plots_top_row, 3)
                ax.set_title('Batteries')
                ax.set(ylabel='Stored power in kWh')
                for name, values in batteryLevels.items():
                    ax.plot(xlabels, values, label=name)
                ax.legend()
            else:
                plots_top_row = 2

            # vehicles
            ax = plt.subplot(2, plots_top_row, 1)
            ax.set_title('Vehicles')
            ax.set(ylabel='SOC in %')
            lines = ax.step(xlabels, socs)
            if len(self.constants.vehicles) <= 10:
                ax.legend(lines, sorted(self.constants.vehicles.keys()))

            # charging stations
            ax = plt.subplot(2, plots_top_row, 2)
            ax.set_title('Charging Stations')
            ax.set(ylabel='Power in kW')
            lines = ax.step(xlabels, sum_cs)
            if len(self.constants.charging_stations) <= 10:
                ax.legend(lines, sorted(self.constants.charging_stations.keys()))

            # total power
            ax = plt.subplot(2, 2, 3)
            ax.plot(xlabels, list([sum(cs) for cs in sum_cs]), label="CS")
            for name, values in loads.items():
                ax.plot(xlabels, values, label=name)

            ax.plot(xlabels, totalLoad, label="total")
            # ax.axhline(color='k', linestyle='--', linewidth=1)
            ax.set_title('Power')
            ax.set(ylabel='Power in kW')
            ax.legend()
            ax.xaxis_date() # xaxis are datetime objects

            # price
            ax = plt.subplot(2, 2, 4)
            lines = ax.step(xlabels, prices)
            ax.set_title('Price for 1 kWh')
            ax.set(ylabel='€')
            if len(self.constants.grid_connectors) <= 10:
                ax.legend(lines, sorted(self.constants.grid_connectors.keys()))

            # figure title
            fig = plt.gcf()
            fig.suptitle('Strategy: {}: {}€'.format(strat.description, int(sum(costs))), fontweight='bold')

            fig.autofmt_xdate() # rotate xaxis labels (dates) to fit
            plt.show()
