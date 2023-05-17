from copy import deepcopy
import datetime

from spice_ev import events, util
from spice_ev.strategy import Strategy


class FlexWindow(Strategy):
    """ Charging during given time windows. """
    def __init__(self, components, start_time, **kwargs):
        self.HORIZON = 24  # hours ahead
        self.LOAD_STRAT = "balanced"  # greedy, needy, balanced

        super().__init__(components, start_time, **kwargs)
        assert (len(self.world_state.grid_connectors) == 1), "Only one grid connector supported"
        self.description = "Flex Window ({}, {} hour horizon)".format(
            self.LOAD_STRAT, self.HORIZON)

        if self.LOAD_STRAT == "greedy":
            # charge vehicles in need first, then by order of departure
            self.sort_key = lambda v: (
                v.battery.soc >= v.desired_soc,
                v.estimated_time_of_departure)
        elif self.LOAD_STRAT == "needy":
            # charge vehicles with not much power needed first, may leave more for others
            self.sort_key = lambda v: v.get_delta_soc() * v.battery.capacity
        elif self.LOAD_STRAT == "balanced":
            # default, simple strategy: charge vehicles balanced during windows
            self.sort_key = lambda v: (
                v.battery.soc < v.desired_soc,
                v.estimated_time_of_departure)
        else:
            "Unknown charging strategy: {}".format(self.LOAD_STRAT)

    def step(self):
        """ Calculate charging power in each timestep.

        :return: current time and commands of the charging stations
        :rtype: dict
        """

        gc = list(self.world_state.grid_connectors.values())[0]

        # reset charging station power (nothing charged yet in this timestep)
        for cs in self.world_state.charging_stations.values():
            cs.current_power = 0

        cur_local_generation = {k: -v for k, v in gc.current_loads.items() if v < 0}
        cur_max_power = gc.cur_max_power

        # ---------- GET NEXT EVENTS ---------- #
        timesteps = []

        # look ahead (limited by horizon)
        # get future events and predict fixed load and cost for each timestep
        event_idx = 0
        timesteps_ahead = int(datetime.timedelta(hours=self.HORIZON) / self.interval)

        cur_time = self.current_time - self.interval
        cur_window = gc.window

        for timestep_idx in range(timesteps_ahead):

            cur_time += self.interval
            # peek into future events
            while True:
                try:
                    event = self.world_state.future_events[event_idx]
                except IndexError:
                    # no more events
                    break
                if event.start_time > cur_time:
                    # not this timestep
                    break
                event_idx += 1
                if type(event) == events.GridOperatorSignal:
                    # update GC info
                    cur_max_power = event.max_power or cur_max_power
                    if event.window is not None:
                        cur_window = event.window
                elif type(event) == events.LocalEnergyGeneration:
                    cur_local_generation[event.name] = event.value
                # vehicle events ignored (use vehicle info such as estimated_time_of_departure)

            fixed_load = gc.get_avg_fixed_load(cur_time, self.interval) \
                - sum(cur_local_generation.values())

            # save information for each timestep
            timesteps.append(
                {
                    "timestep_idx": timestep_idx,
                    "power": cur_max_power - fixed_load,
                    "fixed_load": fixed_load,
                    "window": cur_window,
                    "v_load": 0,
                    "total_load": fixed_load
                }
            )

        # read current window from timesteps
        gc.window = timesteps[0]["window"]
        loaded_v2g = False
        if self.LOAD_STRAT == "balanced":
            # load vehicle with balanced strategy
            commands = self.distribute_balanced_vehicles(timesteps)
            # check if there is surplus power available
            if -gc.get_current_load() > self.EPS:
                # add surplus power to vehicle
                commands.update(self.distribute_surplus_to_vehicles())
            else:
                # get commands from V2G
                commands_v2g = self.distribute_balanced_v2g(timesteps)
                # update old commands with V2G commands
                commands.update(commands_v2g)
                # loaded_v2g is True if there was V2G discharge
                loaded_v2g = bool(commands_v2g)
            # if there is surplus: add surplus to batteries. Else load batteries balanced
            if gc.get_current_load() < 0 and not loaded_v2g:
                self.load_surplus_to_batteries()
            else:
                self.distribute_balanced_batteries(timesteps)
        else:
            # charge vehicles with peak shaving strategy
            commands = self.distribute_peak_shaving_vehicles(timesteps)
            # check if there is surplus power available
            if -gc.get_current_load() > self.EPS:
                # add surplus power to vehicle
                commands.update(self.distribute_surplus_power())
            else:
                # get commands from V2G
                commands_v2g = self.distribute_peak_shaving_v2g(timesteps)
                # update old commands with V2G commands
                commands.update(commands_v2g)
                # loaded_v2g is True if there was V2G discharge
                loaded_v2g = bool(commands_v2g)
            # if there is surplus: add surplus to batteries. Else load batteries balanced
            if gc.get_current_load() < 0 and not loaded_v2g:
                self.load_surplus_to_batteries()
            else:
                self.distribute_peak_shaving_batteries(timesteps)

        return {"current_time": self.current_time, "commands": commands}

    def distribute_balanced_vehicles(self, timesteps):
        """ Charge vehicles with balanced method according to time windows.

        :param timesteps: list of dictionaries for each timestep in horizon
        :type timesteps: list
        :return: commands for charging stations
        :rtype: dict
        """

        commands = {}
        gc = list(self.world_state.grid_connectors.values())[0]
        # order vehicles
        vehicles = sorted([v for v in self.world_state.vehicles.values()
                           if (v.connected_charging_station is not None)], key=self.sort_key)

        for vehicle in vehicles:
            cs_id = vehicle.connected_charging_station
            cs = self.world_state.charging_stations[cs_id]
            sim_vehicle = deepcopy(vehicle)
            # simple case: charge balanced during windows
            # try to charge with full power
            cur_time = self.current_time - self.interval
            for ts_info in timesteps:
                cur_time += self.interval
                if cur_time >= sim_vehicle.estimated_time_of_departure:
                    break
                if ts_info["window"]:
                    p = ts_info["power"]
                    p = util.clamp_power(p, sim_vehicle, cs)
                    sim_vehicle.battery.load(self.interval, max_power=p)

            charged_in_window = sim_vehicle.get_delta_soc() <= self.EPS

            min_power = 0
            max_power = util.clamp_power(cs.max_power, sim_vehicle, cs)
            old_soc = vehicle.battery.soc
            safe = vehicle.get_delta_soc() <= self.EPS
            power_vec = [0] * len(timesteps)
            # Compute the optimal maximum power to charge a vehicle to desired SOC
            # For vehicles that cannot be fully charged in charge window, this power is applied to
            # all TS of non-charging windows (if GC bound is not tighter) while during charging TS
            # all remaining power on GC is used.
            # For vehicles that can be fully charged during charge windows, at every TS of a
            # charging window the minimum of this power and the remaining power on the
            # GC is applied.
            while max_power - min_power > self.EPS:
                power = (min_power + max_power) / 2
                sim_vehicle.battery.soc = old_soc

                cur_time = self.current_time - self.interval
                for ts_idx, ts_info in enumerate(timesteps):
                    cur_time += self.interval
                    avg_power = 0
                    if cur_time >= sim_vehicle.estimated_time_of_departure:
                        break

                    if ts_info["window"] == charged_in_window:
                        p = util.clamp_power(min(power, ts_info["power"]), sim_vehicle, cs)
                        avg_power = sim_vehicle.battery.load(
                            self.interval, max_power=p)["avg_power"]
                    elif not charged_in_window and ts_info["window"]:
                        # charging windows not sufficient, charge max during window
                        p = util.clamp_power(ts_info["power"], sim_vehicle, cs)
                        avg_power = sim_vehicle.battery.load(
                            self.interval, max_power=p)["avg_power"]

                    power_vec[ts_idx] = avg_power
                    safe = sim_vehicle.get_delta_soc() <= self.EPS
                    if safe:
                        power_vec[ts_idx + 1:] = [0] * (len(timesteps) - ts_idx - 1)
                        break

                if safe:
                    max_power = power
                else:
                    min_power = power

            # The GC may not allow to charge with optimal power during current TS
            power = min(gc.max_power - gc.get_current_load(), power)
            # apply power
            if gc.window:
                p = (power if charged_in_window
                     else gc.cur_max_power - gc.get_current_load())
            else:
                p = 0 if charged_in_window else power
            p = util.clamp_power(p, vehicle, cs)
            avg_power = vehicle.battery.load(self.interval, max_power=p)["avg_power"]
            commands[cs_id] = gc.add_load(cs_id, avg_power)
            cs.current_power += avg_power

            for ts_idx, ts_info in enumerate(timesteps):
                ts_info["power"] -= power_vec[ts_idx]

        return commands

    def distribute_balanced_batteries(self, timesteps):
        """ Charge/discharge stationary batteries with balanced method according to time windows.

        :param timesteps: list of dictionaries for each timestep in horizon
        :type timesteps: list
        """

        gc = list(self.world_state.grid_connectors.values())[0]

        batteries = [b for b in self.world_state.batteries.values()]
        cur_window = gc.window
        sim_batteries = deepcopy(batteries)

        # charge/discharge batteries
        min_power = - gc.max_power
        max_power = gc.max_power - gc.get_current_load()

        window_timesteps = [item for item in timesteps if item["window"] is cur_window]
        new_timesteps = []
        for i, row in enumerate(window_timesteps):
            if window_timesteps[i]["timestep_idx"] != i:
                break
            new_timesteps.append(row)
        old_soc = [b.soc for b in sim_batteries]

        total_power = 0
        while max_power - min_power > self.EPS:
            total_power = (min_power + max_power) / 2
            # reset soc
            for i, b in enumerate(sim_batteries):
                b.soc = old_soc[i]

            # calculate needed power to load battery
            for ts_info in new_timesteps:
                for b in sim_batteries:
                    if cur_window:
                        if b.soc > 1 - self.EPS:
                            # already charged
                            break
                    else:
                        if b.soc < 0 + self.EPS:
                            # already discharged
                            break
                    total_power = (0 if total_power < b.min_charging_power else total_power)
                    if total_power > 0:
                        p = total_power / len(sim_batteries)
                        if cur_window:
                            b.load(self.interval, max_power=p)["avg_power"]
                        else:
                            b.unload(self.interval, max_power=p)["avg_power"]
            if cur_window:
                at_limit = all(
                    [b.soc >= (1 - self.EPS) for b in sim_batteries])
            else:
                at_limit = all(
                    [b.soc <= (0 + self.EPS) for b in sim_batteries])

            if at_limit:
                max_power = total_power
            else:
                min_power = total_power
        # actual charge/ discharge
        for b_id, battery in self.world_state.batteries.items():
            if cur_window:
                avail_power = 0 if total_power < battery.min_charging_power else total_power
                p = avail_power / len(batteries)
                if avail_power > 0:
                    charge = battery.load(self.interval, max_power=p)["avg_power"]
                    gc.add_load(b_id, charge)
                    timesteps[0]["total_load"] += charge
            else:
                if total_power < 0:
                    discharge = 0
                else:
                    p = total_power / len(batteries)
                    discharge = battery.unload(self.interval, max_power=p)["avg_power"]
                gc.add_load(b_id, -discharge)
                timesteps[0]["total_load"] -= discharge

    def distribute_balanced_v2g(self, timesteps):
        """ Charge/discharge vehicles with v2g with balanced method according to time windows.

        :param timesteps: list of dictionaries for each timestep in horizon
        :type timesteps: list
        :return: commands for charging stations
        :rtype: dict
        """

        commands = {}
        gc = list(self.world_state.grid_connectors.values())[0]
        # get all vehicles that are connected in this step and order vehicles
        vehicles = sorted([v for v in self.world_state.vehicles.values()
                           if (v.connected_charging_station is not None)
                           and (v.vehicle_type.v2g)], key=self.sort_key)

        cur_window = timesteps[0]["window"]
        window = cur_window

        for vehicle in vehicles:
            cs_id = vehicle.connected_charging_station
            cs = self.world_state.charging_stations[cs_id]
            sim_vehicle = deepcopy(vehicle)
            cur_time = self.current_time - self.interval
            max_discharge_power = (sim_vehicle.battery.loading_curve.max_power
                                   * sim_vehicle.vehicle_type.v2g_power_factor)

            # check if vehicles can be loaded until desired_soc in connected timesteps
            old_soc = vehicle.battery.soc
            connected_timesteps = []
            window_change = 0
            # get connected timesteps and count number of window changes
            for ts_info in timesteps:
                cur_time += self.interval
                if sim_vehicle.estimated_time_of_departure < cur_time:
                    break
                if ts_info["window"] != window:
                    window_change += 1
                    window = ts_info["window"]
                connected_timesteps.append(ts_info)

            # check if vehicle ends up with desired soc, adjust min_soc accordingly
            if not cur_window and window_change >= 1:
                min_soc = self.DISCHARGE_LIMIT
                max_soc = 1
                while max_soc - min_soc > self.EPS:
                    discharge_limit = (max_soc + min_soc) / 2
                    for ts_info in connected_timesteps:
                        if ts_info["window"]:
                            p = ts_info["power"] + ts_info["fixed_load"] - ts_info["total_load"]
                            p = util.clamp_power(p, sim_vehicle, cs)
                            sim_vehicle.battery.load(self.interval, max_power=p)["avg_power"]
                        else:
                            p = min(cs.max_power, max_discharge_power)
                            sim_vehicle.battery.unload(
                                self.interval, max_power=p, target_soc=discharge_limit)["avg_power"]
                    if sim_vehicle.battery.soc <= sim_vehicle.desired_soc - self.EPS:
                        min_soc = discharge_limit
                    else:
                        max_soc = discharge_limit
                    sim_vehicle.battery.soc = old_soc
            elif not cur_window and not window_change:
                discharge_limit = sim_vehicle.desired_soc

            if not cur_window and sim_vehicle.battery.soc <= discharge_limit:
                break
            # filter current window
            window_timesteps = [item for item in connected_timesteps if item["window"]
                                is cur_window]
            new_timesteps = []
            for i, row in enumerate(window_timesteps):
                if window_timesteps[i]["timestep_idx"] != i:
                    break
                new_timesteps.append(row)

            # calculate power to charge / discharge
            min_power = 0
            if cur_window:
                max_power = min(cs.max_power, gc.max_power - gc.get_current_load())
            else:
                max_power = min(cs.max_power, gc.max_power + gc.get_current_load())
            total_power = 0
            while max_power - min_power > self.EPS:
                total_power = (min_power + max_power) / 2
                # reset soc
                sim_vehicle.battery.soc = old_soc
                peak = []
                for ts_info in new_timesteps:
                    if cur_window:
                        if sim_vehicle.battery.soc >= 1 - self.EPS:
                            # already charged
                            break
                    else:
                        if sim_vehicle.battery.soc < discharge_limit + self.EPS:
                            # already discharged
                            break
                    if total_power > 0:
                        if cur_window:
                            power = util.clamp_power(total_power, sim_vehicle, cs)
                            load = sim_vehicle.battery.load(
                                self.interval, max_power=power)["avg_power"]
                            peak.append(load)
                        else:
                            power = util.clamp_power(total_power, sim_vehicle, cs)
                            power = min(power, max_discharge_power)
                            sim_vehicle.battery.unload(
                                self.interval, max_power=power, target_soc=discharge_limit
                            )["avg_power"]

                at_limit = sim_vehicle.battery.soc >= (1 - self.EPS)
                if at_limit:
                    max_power = total_power
                else:
                    min_power = total_power
            # apply power
            if cur_window:
                if total_power <= 0:
                    charge = 0
                else:
                    power = util.clamp_power(total_power, vehicle, cs)
                    charge = vehicle.battery.load(self.interval, max_power=power)["avg_power"]
                commands[cs_id] = gc.add_load(cs_id, charge)
                cs.current_power += charge
                timesteps[0]["total_load"] += charge
            if not cur_window:
                if total_power <= 0:
                    discharge = 0
                else:
                    power = util.clamp_power(total_power, vehicle, cs)
                    power = min(power, max_discharge_power)
                    discharge = vehicle.battery.unload(
                        self.interval, max_power=power, target_soc=discharge_limit
                    )["avg_power"]
                commands[cs_id] = gc.add_load(cs_id, -discharge)
                cs.current_power -= discharge
                timesteps[0]["total_load"] -= discharge

        return commands

    def distribute_peak_shaving_vehicles(self, timesteps):
        """ Charge vehicles with peak shaving method according to time windows.

        :param timesteps: list of dictionaries for each timestep in horizon
        :type timesteps: list
        :return: commands for charging stations
        :rtype: dict
        """

        commands = {}
        gc = list(self.world_state.grid_connectors.values())[0]
        # get all vehicles that are connected in this step and order vehicles
        vehicles = sorted([v for v in self.world_state.vehicles.values()
                           if v.connected_charging_station is not None],
                          key=self.sort_key)

        # what happens when all vehicles are charged with maximum power during charging windows?
        sim_vehicles = deepcopy(vehicles)
        cur_vehicles = sim_vehicles

        # check if battery can be fully charged within time windows in horizon with max power
        cur_time = self.current_time - self.interval
        for ts_info in timesteps:
            cur_time += self.interval
            # get all vehicle events that depart later than current time
            cur_vehicles = [v for v in cur_vehicles if (v.estimated_time_of_departure > cur_time)
                            and (v.battery.soc < v.desired_soc)]
            # calculate total energy needed
            cur_needed = sum([v.get_energy_needed(full=True) for v in cur_vehicles])
            # if there are no vehicles to charge or no energy needed
            if not cur_vehicles or cur_needed < self.EPS:
                # no vehicles or no energy need: skip check
                break

            if ts_info["window"]:
                self.distribute_power(cur_vehicles, ts_info["power"], cur_needed)

        charged_in_window = all([v.get_delta_soc() < self.EPS for v in sim_vehicles])

        # can be charged within windows: reset SoC
        for i, v in enumerate(sim_vehicles):
            v.battery.soc = vehicles[i].battery.soc
        new_timesteps = [ts for ts in timesteps if ts["window"] == charged_in_window]

        old_soc = [v.battery.soc for v in sim_vehicles]
        min_total_power = -gc.max_power
        max_total_power = gc.max_power

        # find the right power to charge the precalculated soc
        while max_total_power - min_total_power > self.EPS:
            total_power = (min_total_power + max_total_power) / 2

            # reset SoC
            cur_vehicles = sim_vehicles
            for i, v in enumerate(sim_vehicles):
                v.battery.soc = old_soc[i]

            cur_time = self.current_time - self.interval
            index = -1
            for ts_info in new_timesteps:
                index += 1
                cur_time += self.interval
                new_cur_vehicles = []
                for v in cur_vehicles:
                    # if departure time is still ahead
                    if (v.estimated_time_of_departure > cur_time) \
                            and (v.battery.soc < v.desired_soc):
                        # add vehicle to new vehicle list
                        new_cur_vehicles.append(v)
                    elif v.get_delta_soc() > self.EPS:
                        break
                cur_vehicles = new_cur_vehicles
                # sum up energy needed in this timestep
                cur_needed = sum([v.get_energy_needed(full=True) for v in cur_vehicles])
                if not cur_vehicles or cur_needed < self.EPS:
                    # no vehicles or no energy need: skip simulation
                    break
                self.distribute_power(cur_vehicles, total_power - ts_info["fixed_load"], cur_needed)

            safe = all([v.get_delta_soc() < self.EPS for v in sim_vehicles])
            if safe:
                max_total_power = total_power
            else:
                min_total_power = total_power

        # apply power
        total_energy_needed = sum([v.get_energy_needed(full=True) for v in vehicles])

        if gc.window == charged_in_window:
            commands = self.distribute_power(
                vehicles, total_power - gc.get_current_load(), total_energy_needed)
        elif not charged_in_window and gc.window:
            commands = self.distribute_power(
                vehicles, gc.max_power - gc.get_current_load(), total_energy_needed)
        for cs_id, power in commands.items():
            cs = self.world_state.charging_stations[cs_id]
            old_power = power
            commands[cs_id] = gc.add_load(cs_id, power)
            timesteps[0]["v_load"] += power
            timesteps[0][cs_id] = power
            timesteps[0]["total_load"] += power
            assert commands[cs_id] == old_power
            cs.current_power += commands[cs_id]

        return commands

    def distribute_peak_shaving_batteries(self, timesteps):
        """ Charge/discharge batteries with peak shaving method according to time windows.

        :param timesteps: list of dictionaries for each timestep in horizon
        :type timesteps: list
        """

        discharging_stations = []
        batteries = [b for b in self.world_state.batteries.values()
                     if b.parent is not None]

        gc = list(self.world_state.grid_connectors.values())[0]
        cur_window = gc.window

        sim_batteries = deepcopy(batteries)

        is_charging_mode = False
        if cur_window:
            is_charging_mode = True

        # charge/discharge batteries
        if is_charging_mode:
            # charge battery
            min_total_power = -gc.max_power
            max_total_power = gc.max_power

            window_timesteps = [item for item in timesteps if item["window"]]
            new_timesteps = []
            for i, row in enumerate(window_timesteps):
                if window_timesteps[i]["timestep_idx"] != i:
                    break
                new_timesteps.append(row)
            old_soc = [b.soc for b in sim_batteries]

            while max_total_power - min_total_power > self.EPS:
                total_power = (min_total_power + max_total_power) / 2
                # reset soc
                for i, b in enumerate(sim_batteries):
                    b.soc = old_soc[i]

                # calculate needed power to load battery
                for ts_info in new_timesteps:
                    cur_avail_power = total_power - ts_info["total_load"]
                    for b in sim_batteries:
                        if b.soc > 1 - self.EPS:
                            # already charged
                            break
                        cur_avail_power = (0 if cur_avail_power < b.min_charging_power
                                           else cur_avail_power)
                        if cur_avail_power > 0:
                            power = cur_avail_power / len(sim_batteries)
                            b.load(self.interval, max_power=power)["avg_power"]

                at_limit = all([b.soc >= (1 - self.EPS) for b in sim_batteries])

                if at_limit:
                    max_total_power = total_power
                else:
                    min_total_power = total_power
            # actual charge
            avail_power = total_power - timesteps[0]["total_load"]
            for b_id, battery in self.world_state.batteries.items():
                avail_power = (0 if avail_power < battery.min_charging_power
                               else avail_power)
                if avail_power > 0:
                    power = avail_power/len(sim_batteries)
                    charge = battery.load(self.interval, max_power=power)["avg_power"]
                    gc.add_load(b_id, charge)
                    timesteps[0]["total_load"] += charge
        else:
            # discharge battery
            no_window_timesteps = [item for item in timesteps if not item["window"]]
            new_timesteps = []
            for i, row in enumerate(no_window_timesteps):
                if no_window_timesteps[i]["timestep_idx"] != i:
                    break
                new_timesteps.append(row)

            min_total_power = -gc.max_power
            max_total_power = gc.max_power

            old_soc = [b.soc for b in sim_batteries]

            while max_total_power - min_total_power > self.EPS:
                total_power = (min_total_power + max_total_power) / 2

                for i, b in enumerate(sim_batteries):
                    b.soc = old_soc[i]

                cur_time = self.current_time - self.interval
                # calculate needed power to load battery
                for ts_info in new_timesteps:
                    cur_time += self.interval
                    cur_needed_power = ts_info["total_load"] - total_power

                    for b in sim_batteries:
                        if b.soc <= self.EPS:
                            break
                        if cur_needed_power > 0:
                            power = cur_needed_power / len(sim_batteries)
                            b.unload(self.interval, max_power=power)["avg_power"]
                at_limit = all([b.soc > (self.EPS) for b in sim_batteries])

                if at_limit:
                    max_total_power = total_power
                else:
                    min_total_power = total_power

            # actual discharge
            needed_power = timesteps[0]["total_load"] - total_power

            for b_id, battery in self.world_state.batteries.items():
                if needed_power < 0:
                    discharge = 0
                else:
                    power = needed_power/len(batteries)
                    discharge = battery.unload(self.interval, max_power=power)["avg_power"]
                discharging_stations.append(b_id)
                gc.add_load(b_id, -discharge)
                timesteps[0]["total_load"] -= discharge

    def distribute_peak_shaving_v2g(self, timesteps):
        """ Charge/discharge vehicles with v2g with peak shaving method according to time windows.

        :param timesteps: list of dictionaries for each timestep in horizon
        :type timesteps: list
        :return: commands for charging stations
        :rtype: dict
        """

        commands = {}
        gc = list(self.world_state.grid_connectors.values())[0]
        # get all vehicles that are connected in this step and order vehicles
        vehicles = sorted([v for v in self.world_state.vehicles.values()
                           if (v.connected_charging_station is not None) and (
                               v.vehicle_type.v2g)], key=self.sort_key)

        cur_window = timesteps[0]["window"]
        cur_time = self.current_time - self.interval

        for vehicle in vehicles:
            sim_vehicle = deepcopy(vehicle)
            cs_id = sim_vehicle.connected_charging_station
            cs = self.world_state.charging_stations[cs_id]
            max_discharge_power = (sim_vehicle.battery.loading_curve.max_power
                                   * sim_vehicle.vehicle_type.v2g_power_factor)

            # check if vehicles can be loaded until desired_soc in connected timesteps
            old_soc = vehicle.battery.soc
            connected_timesteps = []
            window = cur_window
            window_change = 0
            # get connected timesteps and count number of window changes
            for ts_info in timesteps:
                cur_time += self.interval
                if sim_vehicle.estimated_time_of_departure < cur_time:
                    break
                if ts_info["window"] != window:
                    window_change += 1
                    window = ts_info["window"]
                connected_timesteps.append(ts_info)

            # check if vehicle ends up with desired soc, adjust min_soc accordingly
            if not cur_window and window_change >= 1:
                min_soc = self.DISCHARGE_LIMIT
                max_soc = 1
                while max_soc - min_soc > self.EPS:
                    discharge_limit = (max_soc + min_soc) / 2
                    for ts_info in connected_timesteps:
                        if ts_info["window"]:
                            sim_vehicle.battery.load(
                                self.interval, max_power=cs.max_power)["avg_power"]
                        else:
                            power = min(cs.max_power, max_discharge_power)
                            sim_vehicle.battery.unload(
                                self.interval, max_power=power, target_soc=discharge_limit
                            )["avg_power"]
                    if sim_vehicle.battery.soc <= sim_vehicle.desired_soc - self.EPS:
                        min_soc = discharge_limit
                    else:
                        max_soc = discharge_limit
                    sim_vehicle.battery.soc = old_soc
            elif not cur_window and not window_change:
                discharge_limit = sim_vehicle.desired_soc

            if not cur_window and sim_vehicle.battery.soc <= discharge_limit:
                break
            # charge or discharge vehicle battery
            if cur_window:
                # charge battery
                min_total_power = -gc.max_power
                max_total_power = gc.max_power

                window_timesteps = [item for item in timesteps if item["window"] is True]
                old_soc = vehicle.battery.soc

                while max_total_power - min_total_power > self.EPS:
                    total_power = (min_total_power + max_total_power) / 2
                    # reset soc
                    sim_vehicle.battery.soc = old_soc

                    cur_time = self.current_time - self.interval
                    # calculate needed power to load battery
                    for ts_info in window_timesteps:
                        cur_time += self.interval

                        cur_avail_power = total_power - ts_info["total_load"]
                        if sim_vehicle.battery.soc >= 1:
                            # already charged
                            break
                        if cur_avail_power > 0:
                            cur_avail_power = (
                                0 if cur_avail_power <
                                sim_vehicle.vehicle_type.min_charging_power
                                else cur_avail_power)
                            power = util.clamp_power(cur_avail_power, sim_vehicle, cs)
                            sim_vehicle.battery.load(self.interval, max_power=power)["avg_power"]

                    at_limit = sim_vehicle.battery.soc >= (1 - self.EPS)
                    if at_limit:
                        max_total_power = total_power
                    else:
                        min_total_power = total_power
                avail_power = total_power - window_timesteps[0]["total_load"]
                avail_power = (0 if avail_power < vehicle.vehicle_type.min_charging_power
                               else avail_power)
                charge = vehicle.battery.load(self.interval, max_power=avail_power)["avg_power"]
                commands[cs_id] = gc.add_load(cs_id, charge)
                cs.current_power += charge
                timesteps[0]["total_load"] += charge

            else:
                # discharge battery
                no_window_timesteps = [item for item in timesteps if item["window"] is False]

                min_total_power = -gc.max_power
                max_total_power = gc.max_power

                old_soc = sim_vehicle.battery.soc

                while max_total_power - min_total_power > self.EPS:
                    total_power = (min_total_power + max_total_power) / 2
                    # reset soc
                    sim_vehicle.battery.soc = old_soc

                    cur_time = self.current_time - self.interval
                    # calculate needed power to load battery
                    for ts_info in no_window_timesteps:
                        cur_time += self.interval
                        cur_needed_power = (ts_info["fixed_load"] + ts_info["v_load"]) - total_power
                        if sim_vehicle.battery.soc <= discharge_limit:
                            # already discharged
                            break
                        if cur_needed_power > 0:
                            power = min(cur_needed_power, max_discharge_power)
                            sim_vehicle.battery.unload(
                                self.interval, max_power=power, target_soc=discharge_limit
                            )["avg_power"]

                    at_limit = sim_vehicle.battery.soc > (discharge_limit)
                    if at_limit:
                        max_total_power = total_power
                    else:
                        min_total_power = total_power

                needed_power = no_window_timesteps[0]["total_load"] - total_power
                if needed_power < 0:
                    discharge = 0
                else:
                    power = min(needed_power, max_discharge_power)
                    discharge = vehicle.battery.unload(
                        self.interval, max_power=power, target_soc=discharge_limit)["avg_power"]
                commands[cs_id] = gc.add_load(cs_id, -discharge)
                cs.current_power -= discharge
                timesteps[0]["total_load"] -= discharge

        return commands

    def distribute_power(self, vehicles, total_power, total_needed):
        """ Charge vehicle batteries with available power according to *LOAD_STRAT*.

        Supported values for *LOAD_STRAT*:

        * greedy (vehicles charge greedy one after the other)
        * needy (vehicles that need more energy get proportionally more)
        * balanced (all vehicles are allocated the same amount of power)

        :param vehicles: vehicles to dsitribute power to
        :type vehicles: list of :py:class:`~spice_ev.components.Vehicle`
        :param total_power: total available power
        :type total_power: numeric
        :param total_needed: total power needed
        :type total_needed: numeric
        :raises NotImplementedError: if *LOAD_STRAT* is not supported
        :return: commands for charging stations
        :rtype: dict
        """

        commands = {}
        if total_power <= 0 or total_needed <= 0:
            return {}
        for v in vehicles:
            cs_id = v.connected_charging_station
            cs = self.world_state.charging_stations[cs_id]
            if self.LOAD_STRAT == "greedy":
                power = total_power
            elif self.LOAD_STRAT == "needy":
                energy_needed = v.get_energy_needed(full=True)
                f = energy_needed / total_needed if total_needed > 0 else 0
                power = f * total_power
            else:
                raise NotImplementedError
            power = util.clamp_power(power, v, cs)
            # Adjust SOC and return average charging power for a given timedelta
            # and maximum charging power.
            avg_power = v.battery.load(self.interval, max_power=power)["avg_power"]
            commands[cs_id] = avg_power
        return commands

    def load_surplus_to_batteries(self):
        """ Charge batteries with surplus energy.

        :return: energy used to charge batteries
        :rtype: numeric
        """

        total_energy_used = 0
        for b_id, battery in self.world_state.batteries.items():
            gc = self.world_state.grid_connectors[battery.parent]
            gc_current_load = gc.get_current_load()

            # surplus energy: charge
            power = -gc_current_load
            power = 0 if power < battery.min_charging_power else power
            avg_power = battery.load(self.interval, max_power=power)['avg_power']
            gc.add_load(b_id, avg_power)
            total_energy_used += avg_power
        return total_energy_used

    def distribute_surplus_to_vehicles(self):
        """ Distribute surplus power to vehicles.

        :return: charging commands
        :rtype: dict
        """

        commands = dict()
        for vehicle in self.world_state.vehicles.values():
            cs_id = vehicle.connected_charging_station
            if cs_id is None:
                continue
            cs = self.world_state.charging_stations[cs_id]
            gc = self.world_state.grid_connectors[cs.parent]
            gc_surplus = -gc.get_current_load()
            # surplus power
            power = util.clamp_power(gc_surplus, vehicle, cs)
            avg_power = vehicle.battery.load(self.interval, max_power=power)['avg_power']
            commands[cs_id] = gc.add_load(cs_id, avg_power)
            cs.current_power += avg_power
        return commands
