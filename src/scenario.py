#!/usr/bin/env python3

import datetime
import traceback

from src import constants, events, strategy, util


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
        self.interval = datetime.timedelta(minutes=scenario['interval'])

        # compute n_intervals or stop_time
        assert (scenario.get('stop_time') is None) ^ (scenario.get('n_intervals') is None), (
            'Give either stop_time or n_intervals, not both')
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
        options['events'] = self.events
        strat = strategy.class_from_str(strategy_name)(self.constants, self.start_time, **options)

        event_steps = self.events.get_event_steps(self.start_time, self.n_intervals, self.interval)

        socs = []
        costs = []
        prices = []
        results = []
        extLoads = []
        totalLoad = []
        disconnect = []
        totalFeedIn = 0
        unusedFeedIn = 0
        batteryLevels = {k: [] for k in self.constants.batteries.keys()}
        connChargeByTS = []
        gcPowerSchedule = {gcID: [] for gcID in self.constants.grid_connectors.keys()}

        for step_i in range(self.n_intervals):

            width = 10
            display_step = self.n_intervals / (width + 1)
            # only print full steps
            if step_i // display_step != (step_i - 1) // display_step:
                progress = width * (step_i + 1) // self.n_intervals
                print("[{}{}]\r".format(
                    '#' * progress,
                    '.' * (width - progress)
                ), end="", flush=True)

            # run single timestep
            try:
                res = strat.step(event_steps[step_i])
            except Exception as e:
                print('\n', '*'*42)
                print(e)
                print("Aborting simulation in timestep {} ({})".format(
                    step_i + 1, strat.current_time))
                strat.description = "*** {} (ABORTED) ***".format(strat.description)
                traceback.print_exc()
                break
            results.append(res)

            # get current loads
            cost = 0
            price = []
            curLoad = 0
            for gcID, gc in strat.world_state.grid_connectors.items():
                # loads without charging stations (external + feed-in)
                stepLoads = {k: v for k, v in gc.current_loads.items()
                             if k not in self.constants.charging_stations.keys()}
                extLoads.append(stepLoads)
                # sum up loads (with charging stations), compute cost
                gc_load = gc.get_current_load()
                # price in ct/kWh -> get price in EUR
                if gc.cost:
                    cost += util.get_cost(max(gc_load, 0), gc.cost) / 100
                    price.append(util.get_cost(1, gc.cost))
                else:
                    price.append(0)
                curLoad += gc_load

                gcPowerSchedule[gcID].append(gc.target)

                # sum up total feed-in power
                feed_in_keys = self.events.energy_feed_in_lists.keys()
                totalFeedIn -= sum([gc.current_loads.get(k, 0) for k in feed_in_keys])
                # sum up unused feed-in power (negative total power)
                unusedFeedIn -= min(gc.get_current_load(), 0)

            # get SOC and connected CS of all connected vehicles
            cur_cs = []
            cur_dis = []
            cur_socs = []
            for vidx, vid in enumerate(sorted(strat.world_state.vehicles.keys())):
                vehicle = strat.world_state.vehicles[vid]
                if vehicle.connected_charging_station:
                    cur_cs.append(vehicle.connected_charging_station)
                    cur_dis.append(None)
                    cur_socs.append(vehicle.battery.soc)
                    if len(socs) > 0 and socs[-1][vidx] is None:
                        # just arrived -> update disconnect
                        # find departure
                        start_idx = step_i-1
                        while start_idx >= 0 and socs[start_idx][vidx] is None:
                            start_idx -= 1
                        if start_idx < 0:
                            # first charge, no info about old soc
                            continue
                        # get start soc
                        start_soc = socs[start_idx][vidx]
                        # compute linear equation
                        m = (vehicle.battery.soc - start_soc) / (step_i - start_idx - 1)
                        # update timesteps between start and now
                        for idx in range(start_idx, step_i):
                            disconnect[idx][vidx] = m * (idx - start_idx) + start_soc
                else:
                    cur_socs.append(None)
                    cur_dis.append(None)  # placeholder

            # append accumulated info
            socs.append(cur_socs)
            costs.append(cost)
            prices.append(price)
            totalLoad.append(max(curLoad, 0))
            disconnect.append(cur_dis)
            connChargeByTS.append(cur_cs)

            # get battery levels
            for batName, bat in strat.world_state.batteries.items():
                batteryLevels[batName].append(bat.soc / 100 * bat.capacity)

        # next simulation timestep

        print("Costs: {:.2f} €".format(sum(costs)))
        print("Renewable energy feed-in: {} kW, unused: {} kW ({}%)".format(
            round(totalFeedIn),
            round(unusedFeedIn),
            round((unusedFeedIn)*100/totalFeedIn) if totalFeedIn > 0 else 0)
        )
        for batName, values in batteryLevels.items():
            print("Maximum stored power for {}: {:.2f} kW".format(batName, max(values)))

        if options.get('output', None):
            cs_ids = sorted(strat.world_state.charging_stations.keys())
            uc_keys = [
                "work",
                "business",
                "school",
                "shopping",
                "private/ridesharing",
                "leisure",
                "home",
                "hub"
            ]

            round_to_places = 2

            # which SimBEV-Use Cases are in this scenario?
            # group CS by UC name
            cs_by_uc = {}
            for uc_key in uc_keys:
                for cs_id in cs_ids:
                    if uc_key in cs_id:
                        # CS part of UC
                        if uc_key not in cs_by_uc:
                            # first CS of this UC
                            cs_by_uc[uc_key] = []
                        cs_by_uc[uc_key].append(cs_id)

            uc_keys_present = cs_by_uc.keys()

            with open(options['output'], 'w') as output_file:
                # write header
                # general info
                header = ["timestep", "time"]

                # sum of charging power
                header.append("sum power")
                # charging power per use case
                header += ["sum UC {}".format(uc) for uc in uc_keys_present]

                # total number of occupied charging stations
                header.append("# occupied CS")
                # number of occupied CS per UC
                header += ["# occupied UC {}".format(uc) for uc in uc_keys_present]

                # charging power per CS
                header += [str(cs_id) for cs_id in cs_ids]
                output_file.write(','.join(header))

                # write timesteps
                for idx, r in enumerate(results):
                    # general info: timestep index and timestamp
                    row = [idx, r['current_time']]

                    # charging power
                    # get sum of all current CS power
                    row.append(round(sum(r['commands'].values()), round_to_places))
                    # sum up all charging power for each use case
                    row += [round(sum([cs_value for cs_id, cs_value in r['commands'].items()
                                       if cs_id in cs_by_uc[uc_key]]),
                            round_to_places) for uc_key in uc_keys_present]

                    # get total number of occupied CS
                    row.append(len(connChargeByTS[idx]))
                    # get number of occupied CS for each use case
                    row += [
                        sum([1 if uc_key in cs_id else 0
                            for cs_id in connChargeByTS[idx]]) for uc_key in uc_keys_present]

                    # get individual charging power
                    row += [round(r['commands'].get(cs_id, 0), round_to_places) for cs_id in cs_ids]

                    # write row to file
                    output_file.write('\n' + ','.join(map(lambda x: str(x), row)))

        if options.get('visual', False):
            import matplotlib.pyplot as plt

            print('Done. Create plots...')

            sum_cs = []
            xlabels = []

            for r in results:
                xlabels.append(r['current_time'])
                cur_cs = []
                for cs_id in sorted(self.constants.charging_stations):
                    cur_cs.append(r['commands'].get(cs_id, 0.0))
                sum_cs.append(cur_cs)

            # untangle external loads (with feed-in)
            loads = {}
            for i, step in enumerate(extLoads):
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
            # reset color cycle, so lines have same color
            ax.set_prop_cycle(None)
            ax.plot(xlabels, disconnect, '--')
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
            # draw schedule
            for gcID, schedule in gcPowerSchedule.items():
                if any(s is not None for s in schedule):
                    # schedule exists
                    ax.plot(xlabels, schedule, label="Schedule {}".format(gcID))

            ax.plot(xlabels, totalLoad, label="total")
            # ax.axhline(color='k', linestyle='--', linewidth=1)
            ax.set_title('Power')
            ax.set(ylabel='Power in kW')
            ax.legend()
            ax.xaxis_date()  # xaxis are datetime objects

            # price
            ax = plt.subplot(2, 2, 4)
            lines = ax.step(xlabels, prices)
            ax.set_title('Price for 1 kWh')
            ax.set(ylabel='€')
            if len(self.constants.grid_connectors) <= 10:
                ax.legend(lines, sorted(self.constants.grid_connectors.keys()))

            # figure title
            fig = plt.gcf()
            fig.suptitle('Strategy: {}: {}€'.format(
                strat.description, int(sum(costs))), fontweight='bold')

            fig.autofmt_xdate()  # rotate xaxis labels (dates) to fit
            plt.show()
