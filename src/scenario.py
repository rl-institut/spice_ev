#!/usr/bin/env python3

import datetime
import json
import traceback
import os
from warnings import warn

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
        departed_vehicles = {}
        gcWithinPowerLimit = True

        begin = datetime.datetime.now()
        error = None
        for step_i in range(self.n_intervals):

            if options.get("timing", False):
                # show estimated time until finished after each simulation step
                # get time since start
                dt = datetime.datetime.now() - begin
                # compute fraction of work finished
                f = (step_i + 1) / (self.n_intervals + 1)
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

            # process events
            try:
                super(type(strat), strat).step(event_steps[step_i])
            except Exception:
                error = traceback.format_exc()

            # get vehicle SoC at start of timestep
            cur_dis = []
            cur_socs = []
            for vidx, vid in enumerate(sorted(strat.world_state.vehicles.keys())):
                vehicle = strat.world_state.vehicles[vid]
                cur_socs.append(None)
                cur_dis.append(None)
                connected = vehicle.connected_charging_station is not None
                departed = (vehicle.estimated_time_of_departure is None
                            or vehicle.estimated_time_of_departure <= strat.current_time)

                if connected:
                    cur_socs[-1] = vehicle.battery.soc
                else:
                    if departed:
                        if vid not in departed_vehicles:
                            # newly departed: save current soc, make note of departure
                            cur_dis[-1] = vehicle.battery.soc
                            departed_vehicles[vid] = (step_i, vehicle.battery.soc)
                            # just for continuous lines in plot between connected and disconnected
                            if step_i > 0 and socs[-1][vidx] is not None:
                                cur_socs[-1] = vehicle.battery.soc
                    else:
                        # not driving,just standing disconnected
                        cur_dis[-1] = vehicle.battery.soc

                if (connected or not departed) and vid in departed_vehicles:
                    # newly arrived: update disconnect with linear interpolation
                    start_idx, start_soc = departed_vehicles[vid]
                    # compute linear equation
                    m = (vehicle.battery.soc - start_soc) / (step_i - start_idx)
                    # update timesteps between start and now
                    for idx in range(start_idx, step_i):
                        disconnect[idx][vidx] = m * (idx - start_idx) + start_soc
                        cur_dis[-1] = vehicle.battery.soc
                    # remove vehicle from departed list
                    del departed_vehicles[vid]

            socs.append(cur_socs)
            disconnect.append(cur_dis)

            # get battery levels at start of timestep
            for batName, bat in strat.world_state.batteries.items():
                batteryLevels[batName].append(bat.soc * bat.capacity)

            # run strategy for single timestep
            # default: no action
            res = {'current_time': strat.current_time, 'commands': {}}
            try:
                if error is None:
                    res = strat.step()
            except Exception:
                # error during strategy: add dummy result and abort
                error = traceback.format_exc()
            results.append(res)

            # apply battery losses at end of timestep
            strat.apply_battery_losses()

            # get loads during timestep
            for gcID, gc in strat.world_state.grid_connectors.items():

                # get current loads
                cost = 0
                price = 0
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
                try:
                    assert gcWithinPowerLimit, (
                        "{} maximum load exceeded: {} / {}".format(gcID, gc_load, gc.max_power))
                except AssertionError:
                    # abort if GC power limit exceeded
                    error = traceback.format_exc()

                # compute cost: price in ct/kWh -> get price in EUR
                if gc.cost:
                    power = max(gc_load, 0)
                    energy = power / stepsPerHour
                    cost += util.get_cost(energy, gc.cost) / 100
                    price = util.get_cost(1, gc.cost)

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

            if error is not None:
                print('\n', '*'*42)
                print("Aborting simulation in timestep {} ({})".format(
                    step_i + 1, strat.current_time))
                strat.description = "*** {} (ABORTED) ***".format(strat.description)
                print(error)
                break

        # next simulation timestep

        # make variable members of Scenario class to access them in report
        for var in ["batteryLevels", "connChargeByTS", "costs", "disconnect",
                    "extLoads", "feedInPower", "gcPowerSchedule", "gcWindowSchedule", "prices",
                    "results", "socs", "step_i", "stepsPerHour", "strat",
                    "strategy_name", "totalLoad"]:
            setattr(self, var, locals()[var])

        # save reference to negative soc tracker for ease of use in other modules
        self.negative_soc_tracker = strat.negative_soc_tracker

        # summary if desired SoC was not reached anytime
        if strat.desired_counter:
            warn(f"Desired SoC not reached in {strat.desired_counter} cases "
                 f"(with margin of {strat.margin * 100}%: {strat.margin_counter} cases)")

        for gcID in gc_ids:
            print("Energy drawn from {}: {:.0f} kWh, Costs: {:.2f} €".format(gcID,
                                                                             sum(totalLoad[gcID]) /
                                                                             stepsPerHour,
                                                                             sum(costs[gcID])))

        attach_vehicle_soc = options.get("attach_vehicle_soc")
        cost_calculation = options.get("cost_calculation")
        save_timeseries = options.get("save_timeseries")
        save_results = options.get("save_results")
        save_soc = options.get("save_soc")
        testing = options.get("testing")
        visual = options.get("visual")

        if save_results or testing:
            # initialize aggregation variables with empty dicts
            for var in ["avg_drawn", "flex_bands", "total_car_cap", "avg_stand_time",
                        "total_car_energy", "avg_needed_energy", "perc_stand_window",
                        "avg_flex_per_window", "sum_energy_per_window", "avg_total_standing_time"]:
                setattr(self, var, {})

        # check file extensions
        if save_results:
            # general results should be JSON
            ext = os.path.splitext(save_results)[-1]
            if ext != ".json":
                print("File extension mismatch: results file is of type .json")
        if save_soc:
            # vehicle SoC should be CSV
            ext = os.path.splitext(save_soc)[-1]
            if ext != ".csv":
                print("File extension mismatch: SoC timeseries file is of type .csv")
        if save_timeseries:
            # timeseries data should be CSV
            ext = os.path.splitext(save_timeseries)[-1]
            if ext != ".csv":
                print("File extension mismatch: timeseries file is of type .csv")

        for gcID in gc_ids:
            if cost_calculation or save_timeseries:
                # aggregate timeseries info
                agg_ts = report.aggregate_timeseries(self, gcID)
            if save_results or testing:
                # aggregate GC dependent info
                results_file_content = report.aggregate_local_results(
                    strategy_name=strategy_name, scenario=self, gcID=gcID)
            if save_results:
                # write general results to file
                if len(gc_ids) == 1:
                    file_name = save_results
                else:
                    file_name, ext = os.path.splitext(save_results)
                    file_name = f"{file_name}_{util.sanitize(gcID)}{ext}"
                with open(file_name, 'w') as results_file:
                    json.dump(results_file_content, results_file, indent=2)
            if save_timeseries:
                # save power use for each timestep in file
                file_name, ext = os.path.splitext(save_timeseries)
                if len(gc_ids) == 1:
                    file_name = save_timeseries
                else:
                    file_name, ext = os.path.splitext(save_timeseries)
                    file_name = f"{file_name}_{util.sanitize(gcID)}{ext}"
                with open(file_name, 'w') as timeseries_file:
                    # write header
                    timeseries_file.write(','.join(agg_ts["header"]))
                    # write timestep data
                    for row in agg_ts["timeseries"]:
                        timeseries_file.write('\n' + ','.join(map(lambda x: str(x), row)))

        # GC-independent stuff

        if attach_vehicle_soc or save_soc:
            # generate (continuous) SoC of vehicles
            self.vehicle_socs = {}
            report.generate_soc_timeseries(scenario=self)
        if save_soc:
            # write vehicle SoC per timestep to file
            vids = sorted(self.constants.vehicles.keys())
            with open(save_soc, "w") as soc_file:
                # write header
                header = ["timestep", "time"] + vids
                soc_file.write(','.join(header))

                for idx, r in enumerate(results):
                    # general info: timestep index and timestamp
                    # TZ removed for spreadsheet software
                    row = [idx, r['current_time'].replace(tzinfo=None).isoformat()]
                    row += [self.vehicle_socs[vid][idx] for vid in vids]
                    # write row to file
                    soc_file.write('\n' + ','.join(map(lambda x: str(x), row)))

        if visual or testing:
            report.aggregate_global_results(self)
        if visual:
            # plot!
            report.plot(self)
        if testing:
            # metadata, used in tests
            self.testing = {
                "timeseries": {
                    "total_load": self.all_totalLoad,
                    "prices": prices,
                    "schedule": gcWindowSchedule,
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
