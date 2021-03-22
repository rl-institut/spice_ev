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


        for step_i in range(self.n_intervals):
            # run single timestep
            res = strat.step(event_steps[step_i])
            gcs = strat.world_state.grid_connectors.values()

            # get current loads
            currentLoad = 0
            for gc in gcs:
                # loads without charging stations (external + feed-in)
                stepLoads = {k: v for k,v in gc.current_loads.items() if k not in self.constants.charging_stations.keys()}
                extLoads.append(stepLoads)
                # sum up loads (with charging stations)
                currentLoad += gc.get_external_load()

                # sum up feed-in power (negative values)
                totalFeedIn -= sum(min(p, 0) for p in stepLoads.values())
                unusedFeedIn -= min(gc.get_external_load(), 0)

            totalLoad.append(currentLoad)

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
        print("Renewable energy feed-in: {} kW, unused: {} kW ({}%)".format(
            round(totalFeedIn),
            round(unusedFeedIn),
            round((unusedFeedIn)*100/totalFeedIn) if totalFeedIn > 0 else 0)
        )

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
            for name, values in loads.items():
                ax3.plot(xlabels, values, label=name)

            ax3.plot(xlabels, totalLoad, label="total")
            # ax3.axhline(color='k', linestyle='--', linewidth=1)
            ax3.set_title('Power')
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
