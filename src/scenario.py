#!/usr/bin/env python3

import datetime
import json
import traceback
import os

from src import constants, events, strategy, util, report


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
        self.constants_json = json_dict.get('constants') # is there an easier way?

        # compute time stuff
        self.start_time = util.datetime_from_isoformat(scenario['start_time'])
        self.interval = datetime.timedelta(minutes=scenario['interval'])
        self.interval_min = scenario['interval']

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

        # only relevant for cost calculation
        #self.photovoltaics = scenario.get('photovoltaics', None)
        #self.sz = scenario
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

        socs = []
        costs = {gcID: [] for gcID in gc_ids}
        prices = {gcID: [] for gcID in gc_ids}
        results = []
        extLoads = {gcID: [] for gcID in gc_ids}
        totalLoad = {gcID: [] for gcID in gc_ids}
        disconnect = []
        feedInPower = {gcID: [] for gcID in gc_ids}
        stepsPerHour = datetime.timedelta(hours=1) / self.interval
        batteryLevels = {k: [] for k in self.constants.batteries.keys()}
        connChargeByTS = {gcID: [] for gcID in gc_ids}
        gcPowerSchedule = {gcID: [] for gcID in gc_ids}
        gcWindowSchedule = {gcID: [] for gcID in gc_ids}
        gcWithinPowerLimit = True

        #create empty lists for cost calculation (values are filled in during simulation)
        timestamps_list = []
        power_grid_supply_list = []
        price_list = []
        power_fix_load_list = []
        power_feed_in_list = []
        charging_signal_list = []

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

            # get SOC for all vehicle at all timesteps
            cur_dis = []
            cur_socs = []
            for vidx, vid in enumerate(sorted(strat.world_state.vehicles.keys())):
                vehicle = strat.world_state.vehicles[vid]
                if vehicle.connected_charging_station:
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
                        for idx in range(start_idx+1, step_i):
                            disconnect[idx][vidx] = m * (idx - start_idx) + start_soc
                else:
                    cur_socs.append(None)
                    cur_dis.append(None)  # placeholder

            socs.append(cur_socs)
            disconnect.append(cur_dis)

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
                for vidx, vid in enumerate(sorted(strat.world_state.vehicles.keys())):
                    vehicle = strat.world_state.vehicles[vid]
                    if vehicle.connected_charging_station and (strat.world_state.charging_stations[
                            vehicle.connected_charging_station].parent == gcID):
                        cur_cs.append(vehicle.connected_charging_station)

                # append accumulated info
                costs[gcID].append(cost)
                prices[gcID].append(price)
                totalLoad[gcID].append(curLoad)
                feedInPower[gcID].append(curFeedIn)
                connChargeByTS[gcID].append(cur_cs)

            # get battery levels
            for batName, bat in strat.world_state.batteries.items():
                batteryLevels[batName].append(bat.soc * bat.capacity)

        # next simulation timestep

        # adjust step_i: n_intervals or failed simulation step
        step_i += 1

        # make variable members of Scenario class to access them in report
        for var in ["socs", "strat", "costs", "step_i", "prices", "results", "extLoads",
                    "totalLoad", "disconnect", "feedInPower", "stepsPerHour", "batteryLevels",
                    "connChargeByTS", "gcPowerSchedule", "gcWindowSchedule"]:
            setattr(self, var, locals()[var])

        for gcID in gc_ids:
            print("Energy drawn from {}: {:.0f} kWh, Costs: {:.2f} €".format(gcID,
                                                                             sum(totalLoad[gcID]) /
                                                                             stepsPerHour,
                                                                             sum(costs[gcID])))

        if options.get("save_results", False) or options.get("testing", False):

            # initialize aggregation variables with empty dicts
            for var in ["avg_drawn", "flex_bands", "total_car_cap", "avg_stand_time",
                        "total_car_energy", "avg_needed_energy", "perc_stand_window",
                        "avg_flex_per_window", "sum_energy_per_window", "avg_total_standing_time"]:
                setattr(self, var, {})

            if options.get("save_results", False):
                # save general simulation info to JSON file
                ext = os.path.splitext(options["save_results"])
                if ext[-1] != ".json":
                    print("File extension mismatch: results file is of type .json")

            for gcID in gc_ids:
                # stepwise gc specific information is aggregated and curated for results file output
                results_file_content = report.aggregate_local_results(scenario=self, gcID=gcID)

                if options.get("save_results", False):
                    # write to file
                    if len(gc_ids) == 1:
                        file_name = options["save_results"]
                    else:
                        file_name, ext = os.path.splitext(options["save_results"])
                        file_name = f"{file_name}_{gcID}{ext}"
                    with open(file_name, 'w') as results_file:
                        json.dump(results_file_content, results_file, indent=2)

        if options.get("save_timeseries", False):
            # save power use for each timestep in file
            output_path, ext = os.path.splitext(options["save_timeseries"])
            if ext != ".csv":
                print("File extension mismatch: timeseries file is of type .csv")

            for gcID in gc_ids:
                if len(gc_ids) == 1:
                    output_path = options["save_timeseries"]
                else:
                    output_path = f"{file_name}_{gcID}{ext}"

                report.save_gc_timeseries(self, gcID, output_path)

        if options.get("save_soc", False) or options.get("attach_vehicle_soc", False):
            self.vehicle_socs = {}
            report.save_soc_timeseries(scenario=self,
                                       output_path=options.get("save_soc", None))

        if options.get('visual', False) or options.get("testing", False):
            # sum up total load of all grid connectors
            report.aggregate_global_results(self)

            # plot!
            if options.get('visual', False):
                report.plot(self)

        if options.get("testing", False):
            self.testing = {
                "timeseries": {
                    "total_load": self.all_totalLoad,
                    "prices": prices,
                    "schedule": {gcID: gcWindowSchedule[gcID] for gcID in gc_ids},
                    "sum_cs": self.sum_cs,
                    "loads": self.loads
                },
                "max_total_load": max(self.all_totalLoad),
                "avg_flex_per_window": self.avg_flex_per_window,
                "sum_energy_per_window": self.sum_energy_per_window,
                "avg_stand_time": self.avg_stand_time,
                "avg_total_standing_time": self.avg_total_standing_time,
                "avg_needed_energy": self.avg_needed_energy,
                "avg_drawn_power": self.avg_drawn,
                "sum_feed_in_per_h": {gcID: (sum(feedInPower[gcID]) / stepsPerHour)
                                      for gcID in gc_ids},
                "vehicle_battery_cycles": {
                    gcID: (self.total_car_energy[gcID] / self.total_car_cap[gcID])
                    for gcID in gc_ids
                }
            }

        return timestamps_list, power_grid_supply_list, price_list, power_fix_load_list, power_feed_in_list, charging_signal_list