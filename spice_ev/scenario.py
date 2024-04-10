#!/usr/bin/env python3

import datetime
import traceback
from warnings import warn

from spice_ev import components, events, strategy, util, report


class Scenario:
    """ Sets up a scenario from input JSON.

    :param json_dict: input dictionary
    :type json_dict: dict
    :param dir_path: path to the directory
    :type dir_path: str
    """

    def __init__(self, json_dict, dir_path=''):
        # get components (backwards compatibility: used to be called constants)
        components_dict = json_dict.get("components", json_dict.get("constants", {}))
        self.components = components.Components(components_dict)
        # get events (backwards compatibility: some event fields were renamed)
        events_dict = json_dict.get('events', {})
        if "external_load" in events_dict:
            events_dict["fixed_load"] = events_dict["external_load"]
        if "energy_feed_in" in events_dict:
            events_dict["local_generation"] = events_dict["energy_feed_in"]
        self.events = events.Events(events_dict, dir_path)

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

        # only relevant for schedule strategy
        self.core_standing_time = scenario.get('core_standing_time', None)

        # compute average load for each timeslot
        for fixed_load_list in self.events.fixed_load_lists.values():
            gc_id = fixed_load_list.grid_connector_id
            gc = self.components.grid_connectors[gc_id]
            gc.add_avg_fixed_load_week(fixed_load_list, self.interval)

    def run(self, strategy_name, options):
        """ Run the scenario.

        Goes stepwise through all timesteps of the simulation and calls the
        strategy.step method for each timestep. Prints and saves results.

        :param strategy_name: name of the charging strategy
        :type strategy_name: str
        :param options: options of the charging strategy defined in simulate.cfg
        :type options: dict
        """

        options['events'] = self.events
        options['interval'] = self.interval
        options['stop_time'] = self.stop_time
        options['n_intervals'] = self.n_intervals
        options['core_standing_time'] = self.core_standing_time
        strat = strategy.class_from_str(strategy_name)(self.components, self.start_time, **options)

        event_steps = self.events.get_event_steps(self.start_time, self.n_intervals, self.interval)

        gc_ids = self.components.grid_connectors.keys()

        socs = []
        prices = {gcID: [] for gcID in gc_ids}
        results = []
        totalLoad = {gcID: [] for gcID in gc_ids}
        disconnect = []
        fixedLoads = {gcID: [] for gcID in gc_ids}
        stepsPerHour = datetime.timedelta(hours=1) / self.interval
        batteryLevels = {k: [] for k in self.components.batteries.keys()}
        connChargeByTS = {gcID: [] for gcID in gc_ids}
        gcPowerSchedule = {gcID: [] for gcID in gc_ids}
        gcWindowSchedule = {gcID: [] for gcID in gc_ids}
        departed_vehicles = {}
        gcWithinPowerLimit = True
        localGenerationPower = {gcID: [] for gcID in gc_ids}

        begin = datetime.datetime.now()
        error = None
        step_i = -1
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
                error = traceback.format_exc() if error is None else error
            results.append(res)

            # apply battery losses at end of timestep
            strat.apply_battery_losses()

            # get loads during timestep
            for gcID, gc in strat.world_state.grid_connectors.items():

                # get current loads
                cost = 0
                price = 0
                curLoad = 0
                curLocalGeneration = 0

                # loads without charging stations (fixed + local generation)
                stepLoads = {k: v for k, v in gc.current_loads.items()
                             if k not in self.components.charging_stations.keys()}
                fixedLoads[gcID].append(stepLoads)

                # sum up total local generation power
                local_generation_keys = self.events.local_generation_lists.keys()
                curLocalGeneration -= sum([gc.current_loads.get(k, 0)
                                           for k in local_generation_keys])

                # get GC load without local generation power
                gc_load = gc.get_current_load(exclude=local_generation_keys)
                # add local generation power, but don't exceed GC discharge power limit
                gc_load = max(-gc.max_power, gc_load - curLocalGeneration)

                # safety check: GC load within bounds?
                powerLimit = gc.cur_max_power + strat.EPS
                gcWithinPowerLimit = -powerLimit <= gc_load <= powerLimit
                try:
                    assert gcWithinPowerLimit, (
                        "{} maximum load exceeded: {} / {}".format(gcID, gc_load, gc.cur_max_power))
                except AssertionError:
                    # abort if GC power limit exceeded
                    error = traceback.format_exc() if error is None else error

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

                cur_cs = {}
                for vidx, vid in enumerate(sorted(strat.world_state.vehicles.keys())):
                    vehicle = strat.world_state.vehicles[vid]
                    cs_id = vehicle.connected_charging_station
                    cs = strat.world_state.charging_stations.get(cs_id)
                    if cs is not None and cs.parent == gcID:
                        cs_load = gc.current_loads.get(cs_id, 0)
                        cur_cs[cs_id] = cs_load
                        # safety check: CS load within bounds?
                        try:
                            # CS max power
                            assert abs(cs_load) <= cs.max_power+strat.EPS, (
                                f"{cs_id} exceeded maximum charging power: "
                                f"{abs(cs_load)} / {cs.max_power}")
                            """
                            # if charging: must be above min power of CS and vehicle
                            # ignored, since CS/vehicles may charge with high peak power
                            #  during part of the timestep, but have lower average
                            if abs(cs_load) > 0:
                                assert cs.min_power-strat.EPS <= abs(cs_load), (
                                    f"{cs_id} below minimum charging power: "
                                    f"{abs(cs_load)} / {cs.min_power}")
                                vehicle_min_power = vehicle.vehicle_type.min_charging_power
                                assert vehicle_min_power-strat.EPS <= abs(cs_load), (
                                    f"{vid} below minimum charging power: "
                                    f"{abs(cs_load)} / {vehicle_min_power}")
                            """
                        except AssertionError:
                            error = traceback.format_exc() if error is None else error

                # append accumulated info
                prices[gcID].append(price)
                totalLoad[gcID].append(curLoad)
                localGenerationPower[gcID].append(curLocalGeneration)
                connChargeByTS[gcID].append(cur_cs)

            if error is not None:
                print('\n', '*'*42)
                print("Aborting simulation in timestep {} ({})".format(
                    step_i + 1, strat.current_time))
                strat.description = "*** {} (ABORTED) ***".format(strat.description)
                print(error)
                break
        # next simulation timestep

        # end of simulation: increase step_i one last time (no error: step_i == n_intervals)
        step_i += 1

        # make variable members of Scenario class to access them in report
        for var in ["batteryLevels", "connChargeByTS", "disconnect",
                    "fixedLoads", "localGenerationPower", "gcPowerSchedule", "gcWindowSchedule",
                    "prices", "results", "socs", "step_i", "stepsPerHour", "strat",
                    "strategy_name", "totalLoad"]:
            setattr(self, var, locals()[var])

        # save reference to negative soc tracker for ease of use in other modules
        self.negative_soc_tracker = strat.negative_soc_tracker

        # summary if desired SoC was not reached anytime
        if strat.desired_counter:
            warn(f"Desired SoC not reached in {strat.desired_counter} cases "
                 f"(with margin of {strat.margin * 100}%: {strat.margin_counter} cases)")

        for gcID in gc_ids:
            print(f"Energy drawn from {gcID}: {round((sum(totalLoad[gcID])/stepsPerHour), 3)} kWh")

        report.generate_reports(self, options)
