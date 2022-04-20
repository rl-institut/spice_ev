#!/usr/bin/env python3

import datetime
import json
import traceback
import os

from src import constants, events, strategy, util


class Scenario:
    """ Sets up a scenario from input json.

    :param json_dict: input dictionary
    :type json_dict: dict
    :param dir_path: path to the directory
    :type dir_path: str
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
            self.stop_time = util.datetime_from_isoformat(scenario['stop_time'])
            delta = self.stop_time - self.start_time
            self.n_intervals = delta // self.interval

        # minimum SoC to discharge to during v2g
        self.discharge_limit = scenario.get('discharge_limit', 0.5)

        # only relevant for schedule strategy
        self.core_standing_time = scenario.get('core_standing_time', None)

        # compute average load for each timeslot
        for ext_load_list in self.events.external_load_lists.values():
            gc_id = ext_load_list.grid_connector_id
            gc = self.constants.grid_connectors[gc_id]
            gc.add_avg_ext_load_week(ext_load_list, self.interval)

    def run(self, strategy_name, options):
        """
        Run the scenario. Goes stepwise through all timesteps of the simulation and calls the
        strategy.step method for each timestep. Prints and saves results.

        :param strategy_name: name of the charging strategy
        :type strategy_name: str
        :param options: options of the charging strategy defined in simulate.cfg
        :type options: dict
        """
        options['interval'] = self.interval
        options['events'] = self.events
        options['core_standing_time'] = self.core_standing_time
        options['DISCHARGE_LIMIT'] = options.get('DISCHARGE_LIMIT', self.discharge_limit)
        strat = strategy.class_from_str(strategy_name)(self.constants, self.start_time, **options)

        event_steps = self.events.get_event_steps(self.start_time, self.n_intervals, self.interval)

        gc_ids = self.constants.grid_connectors.keys()

        socs = {gcID: [] for gcID in gc_ids}
        costs = {gcID: [] for gcID in gc_ids}
        prices = {gcID: [] for gcID in gc_ids}
        results = []
        extLoads = {gcID: [] for gcID in gc_ids}
        totalLoad = {gcID: [] for gcID in gc_ids}
        disconnect = {gcID: [] for gcID in gc_ids}
        feedInPower = {gcID: [] for gcID in gc_ids}
        stepsPerHour = datetime.timedelta(hours=1) / self.interval
        batteryLevels = {k: [] for k in self.constants.batteries.keys()}
        connChargeByTS = {gcID: [] for gcID in gc_ids}
        gcPowerSchedule = {gcID: [] for gcID in gc_ids}
        gcWindowSchedule = {gcID: [] for gcID in gc_ids}
        gcWithinPowerLimit = True

        begin = datetime.datetime.now()
        for step_i in range(self.n_intervals):

            if options.get("timing", False):
                # show estimated time until finished after each simulation step
                # get time since start
                dt = datetime.datetime.now() - begin
                # compute fraction of work finished
                f = (step_i + 1) / self.n_intervals
                # how much time total?
                total_time = dt / f
                # how much time left?
                eta = total_time - dt
                # remove sub-second resolution from time left
                eta_str = str(eta).split('.')[0]
                print("{} / {}, ETA {}\r".format(
                    step_i, self.n_intervals, eta_str), end="", flush=True)
            else:
                # show progress bar
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
                step_i -= 1
                break
            results.append(res)

            for gcID, gc in strat.world_state.grid_connectors.items():

                # get current loads
                cost = 0
                price = []
                curLoad = 0
                curFeedIn = 0

                # loads without charging stations (external + feed-in)
                stepLoads = {k: v for k, v in gc.current_loads.items()
                             if k not in self.constants.charging_stations.keys()}
                extLoads[gcID].append(stepLoads)

                # sum up total feed-in power
                feed_in_keys = self.events.energy_feed_in_lists.keys()
                curFeedIn -= sum([gc.current_loads.get(k, 0) for k in feed_in_keys])

                # get GC load without feed-in power
                gc_load = gc.get_current_load(exclude=feed_in_keys)
                # add feed-in power, but don't exceed GC discharge power limit
                gc_load = max(-gc.max_power, gc_load - curFeedIn)

                # safety check: GC load within bounds?
                gcWithinPowerLimit &= -gc.max_power-strat.EPS <= gc_load <= gc.max_power+strat.EPS
                if not gcWithinPowerLimit:
                    print('\n', '*'*42)
                    print("GC load exceeded: {} / {}".format(gc_load, gc.max_power))
                    strat.description = "*** {} (ABORTED) ***".format(strat.description)

                # compute cost: price in ct/kWh -> get price in EUR
                if gc.cost:
                    power = max(gc_load, 0)
                    energy = power / stepsPerHour
                    cost += util.get_cost(energy, gc.cost) / 100
                    price.append(util.get_cost(1, gc.cost))
                else:
                    price.append(0)
                curLoad += gc_load

                gcPowerSchedule[gcID].append(gc.target)
                gcWindowSchedule[gcID].append(gc.window)

                # get SOC and connected CS of all connected vehicles at gc

                cur_cs = []
                cur_dis = []
                cur_socs = []
                for vidx, vid in enumerate(sorted(strat.world_state.vehicles.keys())):
                    vehicle = strat.world_state.vehicles[vid]
                    if vehicle.connected_charging_station and (strat.world_state.charging_stations[
                            vehicle.connected_charging_station].parent == gcID):
                        cur_cs.append(vehicle.connected_charging_station)
                        cur_dis.append(None)
                        cur_socs.append(vehicle.battery.soc)
                        if len(socs[gcID]) > 0 and socs[gcID][-1][vidx] is None:
                            # just arrived -> update disconnect
                            # find departure
                            start_idx = step_i-1
                            while start_idx >= 0 and socs[gcID][start_idx][vidx] is None:
                                start_idx -= 1
                            if start_idx < 0:
                                # first charge, no info about old soc
                                continue
                            # get start soc
                            start_soc = socs[gcID][start_idx][vidx]
                            # compute linear equation
                            m = (vehicle.battery.soc - start_soc) / (step_i - start_idx - 1)
                            # update timesteps between start and now
                            for idx in range(start_idx, step_i):
                                disconnect[gcID][idx][vidx] = m * (idx - start_idx) + start_soc
                    else:
                        cur_socs.append(None)
                        cur_dis.append(None)  # placeholder

                # append accumulated info
                socs[gcID].append(cur_socs)
                costs[gcID].append(cost)
                prices[gcID].append(price)
                totalLoad[gcID].append(curLoad)
                disconnect[gcID].append(cur_dis)
                feedInPower[gcID].append(curFeedIn)
                connChargeByTS[gcID].append(cur_cs)

            # get battery levels
            for batName, bat in strat.world_state.batteries.items():
                batteryLevels[batName].append(bat.soc * bat.capacity)

        # next simulation timestep

        # adjust step_i: n_intervals or failed simulation step
        step_i += 1

        for gcID in gc_ids:
            print("Energy drawn from {}: {:.0f} kWh, Costs: {:.2f} €".format(gcID,
                                                                             sum(totalLoad[gcID]) /
                                                                             stepsPerHour,
                                                                             sum(costs[gcID])))

        if options.get("save_timeseries", False) or options.get("save_results", False) \
                or options.get("testing", False):
            # get flexibility band
            from generate_schedule import generate_flex_band

            avg_needed_energy = {}
            avg_flex_per_window = {}
            sum_energy_per_window = {}
            avg_stand_time = {}
            avg_total_standing_time = {}
            perc_stand_window = {}
            total_car_cap = {}
            total_car_energy = {}
            avg_drawn = {}

            for gcID in gc_ids:
                flex = generate_flex_band(self, gcID=gcID)

                if options.get("save_results", False) or options.get("testing", False):
                    if options.get("save_results", False):
                        # save general simulation info to JSON file
                        ext = os.path.splitext(options["save_results"])
                        if ext[-1] != ".json":
                            print("File extension mismatch: results file is of type .json")

                    json_results = {}

                    # gather info about standing and power in specific time windows
                    load_count = [[0] for _ in self.constants.vehicles]
                    load_window = [[] for _ in range(4)]
                    count_window = [[0]*len(self.constants.vehicles) for _ in range(4)]

                    cur_time = self.start_time - self.interval
                    # maximum power (fixed and variable loads)
                    max_fixed_load = 0
                    max_variable_load = 0
                    for idx in range(step_i):
                        cur_time += self.interval
                        time_since_midnight = cur_time - cur_time.replace(hour=0, minute=0)
                        # four equally large timewindows: 04-10, 10-16, 16-22, 22-04
                        # shift time by four hours
                        shifted_time = time_since_midnight - datetime.timedelta(hours=4)
                        # compute window index
                        widx = (shifted_time // datetime.timedelta(hours=6)) % 4

                        load_window[widx].append((flex["max"][idx] - flex["min"][idx],
                                                  totalLoad[gcID][idx]))
                        count_window[widx] = list(map(
                            lambda c, t: c + (t is not None),
                            count_window[widx], socs[gcID][idx]))

                        for i, soc in enumerate(socs[gcID][idx]):
                            if soc is None and load_count[i][-1] > 0:
                                load_count[i].append(0)
                            else:
                                load_count[i][-1] += (soc is not None)

                        fixed_load = sum([v for k, v in extLoads[gcID][idx].items() if
                                          k in self.events.external_load_lists or
                                          k in self.events.energy_feed_in_lists])
                        max_fixed_load = max(max_fixed_load, fixed_load)
                        var_load = totalLoad[gcID][idx] - fixed_load
                        max_variable_load = max(max_variable_load, var_load)

                    # avg flex per window
                    avg_flex_per_window[gcID] = [sum([t[0] for t in w]) / len(w) if w else 0 for w
                                                 in load_window]
                    json_results["avg flex per window"] = {
                        "04-10": avg_flex_per_window[gcID][0],
                        "10-16": avg_flex_per_window[gcID][1],
                        "16-22": avg_flex_per_window[gcID][2],
                        "22-04": avg_flex_per_window[gcID][3],
                        "unit": "kW",
                        "info": "Average flexible power range per time window"
                    }

                    # sum of used energy per window
                    sum_energy_per_window[gcID] = [sum([t[1] for t in w]) / stepsPerHour for w in
                                                   load_window]
                    json_results["sum of energy per window"] = {
                        "04-10": sum_energy_per_window[gcID][0],
                        "10-16": sum_energy_per_window[gcID][1],
                        "16-22": sum_energy_per_window[gcID][2],
                        "22-04": sum_energy_per_window[gcID][3],
                        "unit": "kWh",
                        "info": "Total drawn energy per time window"
                    }
                    if strat.negative_soc_tracker:
                        json_results["vehicles with negative soc"] = strat.negative_soc_tracker

                    # avg standing time
                    # don't use info from flex band, as standing times might be interleaved
                    # remove last empty standing count
                    for counts in load_count:
                        if counts[-1] == 0:
                            counts = counts[:-1]
                    num_loads = sum(map(len, load_count))
                    if num_loads > 0:
                        avg_stand_time[gcID] = sum(map(sum, load_count)) / stepsPerHour / num_loads
                    else:
                        avg_stand_time[gcID] = 0
                    # avg total standing time
                    # count per car: list(zip(*count_window))
                    total_standing = sum(map(sum, count_window))
                    avg_total_standing_time[gcID] = total_standing / len(
                        self.constants.vehicles) / stepsPerHour
                    json_results["avg standing time"] = {
                        "single": avg_stand_time[gcID],
                        "total": avg_total_standing_time,
                        "unit": "h",
                        "info": "Average duration of a single standing event and "
                                "average total time standing time of all vehicles"
                    }

                    # percent of standing time in time window
                    perc_stand_window[gcID] = list(map(
                        lambda x: x * 100 / total_standing if total_standing > 0 else 0,
                        map(sum, count_window)))
                    json_results["standing per window"] = {
                        "04-10": perc_stand_window[gcID][0],
                        "10-16": perc_stand_window[gcID][1],
                        "16-22": perc_stand_window[gcID][2],
                        "22-04": perc_stand_window[gcID][3],
                        "unit": "%",
                        "info": "Share of standing time per time window"
                    }

                    # avg needed energy per standing period
                    intervals = flex["intervals"]
                    if intervals:
                        avg_needed_energy[gcID] = sum([i["needed"] / i["num_cars_present"] for i in
                                                      intervals]) / len(intervals)
                    else:
                        avg_needed_energy[gcID] = 0
                    json_results["avg needed energy"] = {
                        # avg energy per standing period and vehicle
                        "value": avg_needed_energy[gcID],
                        "unit": "kWh",
                        "info": "Average amount of energy needed to reach the desired SoC"
                                " (averaged over all vehicles and charge events)"
                    }

                    # power peaks (fixed loads and variable loads)
                    if any(totalLoad[gcID]):
                        json_results["power peaks"] = {
                            "fixed": max_fixed_load,
                            "variable": max_variable_load,
                            "total": max(totalLoad[gcID]),
                            "unit": "kW",
                            "info": "Maximum drawn power, by fixed loads (building, PV),"
                                    " variable loads (charging stations, stationary batteries) "
                                    "and all loads"
                        }

                    # average drawn power
                    avg_drawn[gcID] = sum(totalLoad[gcID]) / step_i if step_i > 0 else 0
                    json_results["avg drawn power"] = {
                        "value": avg_drawn[gcID],
                        "unit": "kW",
                        "info": "Drawn power, averaged over all time steps"
                    }

                    # total feed-in energy
                    json_results["feed-in energy"] = {
                        "value": sum(feedInPower[gcID]) / stepsPerHour,
                        "unit": "kWh",
                        "info": "Total energy from renewable energy sources"
                    }

                    # battery sizes
                    for b in batteryLevels.values():
                        if any(b):
                            bat_dict = {batName: max(values) for batName, values in
                                        batteryLevels.items()}
                            bat_dict.update({
                                "unit": "kWh",
                                "info": "Maximum stored energy in each battery by name"
                            })
                            json_results["max. stored energy in batteries"] = bat_dict

                    # charging cycles
                    # stationary batteries
                    total_bat_cap = 0
                    for batID, battery in self.constants.batteries.items():
                        if self.constants.batteries[batID].parent == gcID:
                            if battery.capacity > 2**63:
                                # unlimited capacity
                                max_cap = max(batteryLevels[batID])
                                print("Battery {} is unlimited, set capacity to {} kWh".format(
                                    batID, max_cap))
                                total_bat_cap += max_cap
                            else:
                                total_bat_cap += battery.capacity
                    if total_bat_cap:
                        total_bat_energy = 0
                        for loads in extLoads[gcID]:
                            for batID in self.constants.batteries.keys():
                                if self.constants.batteries[batID].parent == gcID:
                                    total_bat_energy += max(loads.get(batID, 0), 0) / stepsPerHour
                        json_results["stationary battery cycles"] = {
                            "value": total_bat_energy / total_bat_cap,
                            "unit": None,
                            "info": "Number of load cycles of stationary batteries (averaged)"
                        }
                    # vehicles
                    total_car_cap[gcID] = sum([v.battery.capacity for v in
                                               self.constants.vehicles.values()])
                    total_car_energy[gcID] = sum([sum(map(
                        lambda v: max(v, 0), r["commands"].values())) for r in results])
                    json_results["all vehicle battery cycles"] = {
                        "value": total_car_energy[gcID]/total_car_cap[gcID],
                        "unit": None,
                        "info": "Number of load cycles per vehicle (averaged)"
                    }

                    if options.get("save_results", False):
                        # write to file
                        if len(gc_ids) == 1:
                            file_name = options["save_results"]
                        else:
                            file_name, ext = os.path.splitext(options["save_results"])
                            file_name = f"{file_name}_{gcID}{ext}"
                        with open(file_name, 'w') as results_file:
                            json.dump(json_results, results_file, indent=2)

        if options.get("save_timeseries", False):
            # save power use for each timestep in file

            # check file extension
            file_name, ext = os.path.splitext(options["save_timeseries"])
            if ext != ".csv":
                print("File extension mismatch: timeseries file is of type .csv")

            for gcID in gc_ids:
                if len(gc_ids) == 1:
                    filename = options["save_timeseries"]
                else:
                    filename = f"{file_name}_{gcID}{ext}"

                cs_ids = sorted(item for item in strat.world_state.charging_stations.keys() if
                                self.constants.charging_stations[item].parent == gcID)

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

                scheduleKeys = []
                for gcID in sorted(gcPowerSchedule.keys()):
                    if any(s is not None for s in gcPowerSchedule[gcID]):
                        scheduleKeys.append(gcID)

                # any loads except CS present?
                hasExtLoads = any(extLoads)

                with open(filename, 'w') as timeseries_file:
                    # write header
                    # general info
                    header = ["timestep", "time"]
                    # price
                    if any(prices):
                        # external loads (e.g., building)
                        header.append("price [EUR/kWh]")
                    # grid power
                    header.append("grid power [kW]")
                    # external loads
                    if hasExtLoads:
                        # external loads (e.g., building)
                        header.append("ext.load [kW]")
                    # feed-in
                    if any(feedInPower):
                        header.append("feed-in [kW]")
                    # batteries
                    if self.constants.batteries:
                        header += ["battery power [kW]", "bat. stored energy [kWh]"]
                    # flex + schedule
                    header += ["flex min [kW]", "flex base [kW]", "flex max [kW]"]
                    header += ["schedule {} [kW]".format(gcID) for gcID in scheduleKeys]
                    header += ["window {}".format(gcID) for gcID in scheduleKeys]
                    # sum of charging power
                    header.append("sum CS power")
                    # charging power per use case
                    header += ["sum UC {}".format(uc) for uc in uc_keys_present]
                    # total number of occupied charging stations
                    header.append("# occupied CS")
                    # number of occupied CS per UC
                    header += ["# occupied UC {}".format(uc) for uc in uc_keys_present]
                    # charging power per CS
                    header += [str(cs_id) for cs_id in cs_ids]
                    timeseries_file.write(','.join(header))

                    # write timesteps
                    for idx, r in enumerate(results):
                        # general info: timestep index and timestamp
                        # TZ removed for spreadsheet software
                        row = [idx, r['current_time'].replace(tzinfo=None)]
                        # price
                        if any(prices[gcID]):
                            row.append(round(prices[gcID][idx][0], round_to_places))
                        # grid power (negative since grid power is fed into system)
                        row.append(-1 * round(totalLoad[gcID][idx], round_to_places))
                        # external loads
                        if hasExtLoads:
                            sumExtLoads = sum([
                                v for k, v in extLoads[gcID][idx].items()
                                if k in self.events.external_load_lists])
                            row.append(round(sumExtLoads, round_to_places))
                        # feed-in (negative since grid power is fed into system)
                        if any(feedInPower):
                            row.append(-1 * round(feedInPower[gcID][idx], round_to_places))
                        # batteries
                        if self.constants.batteries:
                            current_battery = {}
                            for batID in batteryLevels:
                                if self.constants.batteries[batID].parent == gcID:
                                    current_battery.update({batID: batteryLevels[batID]})
                            row += [
                                # battery power
                                round(sum([
                                    v for k, v in extLoads[gcID][idx].items()
                                    if k in self.constants.batteries]),
                                    round_to_places),
                                # battery levels
                                # get connected battery
                                round(
                                    sum([levels[idx] for levels in current_battery.values()]),
                                    round_to_places
                                )
                            ]
                        # flex
                        row += [
                            round(flex["min"][idx], round_to_places),
                            round(flex["base"][idx], round_to_places),
                            round(flex["max"][idx], round_to_places)
                        ]
                        # schedule + window schedule
                        row += [
                            round(gcPowerSchedule[gcID][idx], round_to_places)
                            for gcID in scheduleKeys]
                        row += [
                            round(gcWindowSchedule[gcID][idx], round_to_places)
                            for gcID in scheduleKeys]
                        # charging power
                        # get sum of all current CS power that are connected to gc
                        gc_commands = {}
                        if r['commands']:
                            for k, v in r["commands"].items():
                                if k in cs_ids:
                                    gc_commands.update({k: v})
                        row.append(round(sum(gc_commands.values()), round_to_places))
                        # sum up all charging power at gc for each use case
                        row += [round(sum([cs_value for cs_id, cs_value in gc_commands.items()
                                           if cs_id in cs_by_uc[uc_key]]),
                                round_to_places) for uc_key in uc_keys_present]
                        # get total number of occupied CS that are connected to gc
                        row.append(len(connChargeByTS[gcID][idx]))
                        # get number of occupied CS at gc for each use case
                        row += [
                            sum([1 if uc_key in cs_id else 0
                                for cs_id in connChargeByTS[gcID][idx]]) for uc_key in
                            uc_keys_present]
                        # get individual charging power of cs_id that is connected to gc
                        row += [round(gc_commands.get(cs_id, 0), round_to_places) for cs_id in
                                cs_ids]
                        # write row to file
                        timeseries_file.write('\n' + ','.join(map(lambda x: str(x), row)))

        if options.get("save_soc", False):
            # save soc of each vehicle in one file

            # check file extension
            ext = os.path.splitext(options["save_soc"])[-1]
            if ext != ".csv":
                print("File extension mismatch: timeseries file is of type .csv")
            with open(options['save_soc'], 'w') as soc_file:
                # write header
                header_s = ["timestep", "time"]
                for vidx, vid in enumerate(sorted(self.constants.vehicles.keys())):
                    header_s.append(vid)
                soc_file.write(','.join(header_s))

                sum_soc = []
                for gcID, soc in socs.items():
                    soc = [[0 if x is None else x for x in line] for line in soc]
                    if not sum_soc:
                        sum_soc = soc
                    else:
                        sum_soc = [[i1+j1 for i1, j1 in zip(i, j)] for i, j in zip(sum_soc, soc)]

                for idx, r in enumerate(results):
                    # general info: timestep index and timestamp
                    # TZ removed for spreadsheet software
                    row_s = [idx, r['current_time'].replace(tzinfo=None)]
                    cur_soc = sum_soc[idx]
                    for i, j in enumerate(cur_soc):
                        row_s += [j]

                    # write row to file
                    soc_file.write('\n' + ','.join(map(lambda x: str(x), row_s)))

        # calculate results
        if options.get('visual', False) or options.get("testing", False):

            # sum up total load of all grid connectors
            all_totalLoad = []
            for gcID in gc_ids:
                if not all_totalLoad:
                    all_totalLoad = totalLoad[gcID]
                else:
                    all_totalLoad = list(map(lambda x, y: x+y, all_totalLoad, totalLoad[gcID]))

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
            for gcID in gc_ids:
                loads[gcID] = {}
                for i, step in enumerate(extLoads[gcID]):
                    for k, v in step.items():
                        if k not in loads[gcID]:
                            # new key, not present before
                            loads[gcID][k] = [0] * i
                        loads[gcID][k].append(v)
                    for k in loads[gcID].keys():
                        if k not in step:
                            # old key not in current step
                            loads[gcID][k].append(0)

            # plot!
            if options.get('visual', False):
                import matplotlib.pyplot as plt

                print('Done. Create plots...')

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
                ax.set(ylabel='SoC')
                for gcID, soc in socs.items():
                    lines = ax.step(xlabels, soc)
                    # reset color cycle, so lines have same color
                    ax.set_prop_cycle(None)
                for gcID, dis in disconnect.items():
                    ax.plot(xlabels, dis, '--')
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
                for gcID in gc_ids:
                    for name, values in loads[gcID].items():
                        ax.plot(xlabels, values, label=name)
                # draw schedule
                if strat.uses_window:
                    for gcID, schedule in gcWindowSchedule.items():
                        if all(s is not None for s in schedule):
                            # schedule exists
                            window_values = [v * int(max(totalLoad[gcID])) for v in schedule]
                            ax.plot(xlabels, window_values, label="window {}".format(gcID),
                                    linestyle='--')
                if strat.uses_schedule:
                    for gcID, schedule in gcPowerSchedule.items():
                        if any(s is not None for s in schedule):
                            ax.plot(xlabels, schedule, label="Schedule {}".format(gcID))

                ax.plot(xlabels, all_totalLoad, label="Total")
                ax.set_title('Power')
                ax.set(ylabel='Power in kW')
                ax.legend()
                ax.xaxis_date()  # xaxis are datetime objects

                # price
                ax = plt.subplot(2, 2, 4)
                for gcID, price in prices.items():
                    lines = ax.step(xlabels, price)
                ax.set_title('Price for 1 kWh')
                ax.set(ylabel='€')
                if len(gc_ids) <= 10:
                    ax.legend(lines, sorted(gc_ids))

                # figure title
                fig = plt.gcf()
                fig.suptitle('Strategy: {}'.format(type(strat).__name__), fontweight='bold')

                # fig.autofmt_xdate()  # rotate xaxis labels (dates) to fit
                # autofmt removes some axis labels, so rotate by hand:
                for ax in fig.get_axes():
                    plt.setp(ax.get_xticklabels(), rotation=30, ha='right')

                plt.show()

        # add testing params
        if options.get("testing", False):
            self.testing = {
                "timeseries": {
                    "total_load": all_totalLoad,
                    "prices": prices,
                    "schedule": {gcID: gcWindowSchedule[gcID] for gcID in gc_ids},
                    "sum_cs": sum_cs,
                    "loads": loads
                },
                "max_total_load": max(all_totalLoad),
                "avg_flex_per_window": {gcID: avg_flex_per_window[gcID] for gcID in gc_ids},
                "sum_energy_per_window": {gcID: sum_energy_per_window[gcID] for gcID in gc_ids},
                "avg_stand_time": {gcID: avg_stand_time[gcID] for gcID in gc_ids},
                "avg_total_standing_time": {gcID: avg_total_standing_time[gcID] for gcID in gc_ids},
                "avg_needed_energy": {gcID: avg_needed_energy[gcID] for gcID in gc_ids},
                "avg_drawn_pwer": {gcID: avg_drawn[gcID] for gcID in gc_ids},
                "sum_feed_in_per_h": {gcID: (sum(feedInPower[gcID]) / stepsPerHour) for gcID in
                                      gc_ids},
                "vehicle_battery_cycles": {gcID: (total_car_energy[gcID] / total_car_cap[gcID]) for
                                           gcID in gc_ids}
            }
