from copy import deepcopy
import datetime
import json
import warnings

from spice_ev import events, util
from spice_ev.strategy import Strategy


class PeakLoadWindow(Strategy):
    """ Charging strategy that prioritizes times outside of high load time windows.

    Charge balanced outside of windows. If not sufficient, minimize peak power inside windows.
    """

    def __init__(self, components, start_time, **kwargs):
        self.time_windows = None
        super().__init__(components, start_time, **kwargs)

        self.description = "peak load window"
        self.uses_window = True

        if self.time_windows is None:
            raise Exception("Need time windows for Peak Load Window strategy")
        with open(self.time_windows, 'r') as f:
            self.time_windows = json.load(f)

        # check time windows
        # start year in time windows?
        years = set()
        for grid_operator in self.time_windows.values():
            for window in grid_operator.values():
                years.add(int(window["start"][:4]))
        assert len(years) > 0, "No time windows given"
        # has the scenario year to be replaced because it is not in time windows?
        replace_year = start_time.year not in years
        if replace_year:
            replace_year = start_time.year
            old_year = sorted(years)[0]
            warnings.warn("Time windows do not include scenario year,"
                          f"replacing {old_year} with {replace_year}")
        # cast strings to dates/times, maybe replacing year
        grid_operator = None
        for grid_operator, grid_operator_seasons in self.time_windows.items():
            for season, info in grid_operator_seasons.items():
                start_date = datetime.date.fromisoformat(info["start"])
                if replace_year and start_date.year == old_year:
                    start_date = start_date.replace(year=replace_year)
                info["start"] = start_date
                end_date = datetime.date.fromisoformat(info["end"])
                if replace_year and end_date.year == old_year:
                    end_date = end_date.replace(year=replace_year)
                info["end"] = end_date
                for level, windows in info.get("windows", {}).items():
                    # cast times to datetime.time, store as tuples
                    info["windows"][level] = [
                        (datetime.time.fromisoformat(t[0]), datetime.time.fromisoformat(t[1]))
                        for t in windows]
                self.time_windows[grid_operator][season] = info

        gcs = self.world_state.grid_connectors

        for gc_id, gc in gcs.items():
            if gc.voltage_level is None:
                warnings.warn(f"GC {gc_id} has no voltage level, might not find time window")
                warnings.warn("SETTING VOLTAGE LEVEL TO MV")
                gc.voltage_level = "MV"  # TODO remove
            if gc.grid_operator is None:
                warnings.warn(f"GC {gc_id} has no grid operator, might not find time window")
                # take the first grid operator from time windows
                warnings.warn(f"SETTING GRID OPERATOR TO {grid_operator}")
                gc.grid_operator = grid_operator  # TODO remove

        # perfect foresight for grid and local load events
        local_events = [e for e in self.events.grid_operator_signals
                        if hasattr(e, "grid_connector_id")]
        for name, load_list in self.events.fixed_load_lists.items():
            local_events.extend(load_list.get_events(name, events.FixedLoad))
        for name, local_generation in self.events.local_generation_lists.items():
            local_events.extend(local_generation.get_events(name, events.LocalEnergyGeneration))
        # make these events known in advance
        changed = 0
        for event in local_events:
            old_signal_time = event.signal_time
            event.signal_time = min(event.signal_time, start_time)
            changed += event.signal_time < old_signal_time
        if changed:
            print(changed, "events signaled earlier")
        local_events = sorted(local_events, key=lambda ev: ev.start_time)

        # extend look-ahead to last vehicle departure in scenario
        stop_time = self.stop_time
        for event in self.events.vehicle_events:
            if event.event_type == "arrival":
                stop_time = max(stop_time, event.update["estimated_time_of_departure"])
            elif event.event_type == "departure":
                stop_time = max(stop_time, event.start_time)

        # restructure events (like event_steps): list with events for each timestep
        # also, find highest peak of GC power within time windows
        self.events = []
        current_loads = {}
        peak_power = {}
        peak_time = {gc_id: self.current_time for gc_id in gcs}
        for gc_id, gc in gcs.items():
            current_loads[gc_id] = deepcopy(gc.current_loads)
            peak_power[gc_id] = 0
        cur_time = start_time - self.interval
        event_idx = 0
        while cur_time <= stop_time:
            cur_events = []
            cur_time += self.interval
            while True:
                try:
                    event = local_events[event_idx]
                except IndexError:
                    # no more events
                    break
                if event.start_time > cur_time:
                    # not this timestep
                    break
                event_idx += 1
                cur_events.append(event)
                gc_id = event.grid_connector_id
                gc = gcs[gc_id]
                if type(event) is events.LocalEnergyGeneration:
                    current_loads[gc_id][event.name] = -event.value
                elif type(event) is events.FixedLoad:
                    current_loads[gc_id][event.name] = event.value
            # end of events for this timestep
            # update peak power
            for gc_id, gc in gcs.items():
                is_window = util.datetime_within_time_window(
                    cur_time, self.time_windows[gc.grid_operator], gc.voltage_level)
                gc_sum_loads = sum(current_loads[gc_id].values())
                if is_window and gc_sum_loads > peak_power[gc_id]:
                    # new peak power
                    peak_power[gc_id] = gc_sum_loads
                    peak_time[gc_id] = cur_time
            self.events.append(cur_events)
        self.peak_power = peak_power
        for gc_id, t in peak_time.items():
            if t > self.stop_time:
                warnings.warn(f"Peak power of {peak_power[gc_id]} kW at {gc_id} "
                              f"is not within simulation time, but at {t}")

    def step(self):
        """ Calculate charging power in each timestep.

        :return: current time and commands of the charging stations
        :rtype: dict
        """
        commands = dict()
        # ignore current events
        self.events = self.events[1:]
        for gc_id, gc in self.world_state.grid_connectors.items():
            assert gc.voltage_level is not None
            commands.update(self.step_gc(gc_id, gc))
        return {'current_time': self.current_time, 'commands': commands}

    def step_gc(self, gc_id, gc):
        ts_per_hour = datetime.timedelta(hours=1) / self.interval
        charging_stations = dict()
        # gather all currently connected vehicles
        # also find longest standing time of currently connected vehicles
        vehicles = {}
        max_standing = self.current_time
        for v_id, vehicle in self.world_state.vehicles.items():
            cs_id = vehicle.connected_charging_station
            if cs_id is None:
                continue
            cs = self.world_state.charging_stations.get(cs_id)
            if cs is None:
                continue
            if cs.parent == gc_id:
                vehicles[v_id] = vehicle
                if (
                        vehicle.estimated_time_of_departure is None
                        or vehicle.estimated_time_of_departure <= self.current_time):
                    # no estimated time of departure / should have left already: ignore
                    continue
                if vehicle.desired_soc - vehicle.battery.soc < self.EPS:
                    # charged enough
                    continue
                max_standing = max(max_standing, vehicle.estimated_time_of_departure)

        # find upcoming load windows (not from events), take note of expected GC load
        timesteps_ahead = -((max_standing - self.current_time) // -self.interval)
        timesteps = []
        cur_loads = deepcopy(gc.current_loads)
        cur_max_power = gc.cur_max_power

        # are there stationary batteries for this GC?
        stationary_batteries = {
            bid: b for bid, b in self.world_state.batteries.items() if b.parent == gc_id}

        def within_window(dt):
            return util.datetime_within_time_window(
                dt, self.time_windows[gc.grid_operator], gc.voltage_level)

        gc.window = within_window(self.current_time)
        if stationary_batteries:
            # stat. batteries present: find next change of time window (or end of scenario)
            cur_time = self.current_time + self.interval
            ts_until_window_change = 1
            while within_window(cur_time) == gc.window and cur_time <= self.stop_time:
                cur_time += self.interval
                ts_until_window_change += 1
            if gc.window:
                # may have to append timesteps (all vehicles left during window)
                timesteps_ahead = max(timesteps_ahead, ts_until_window_change)

        cur_time = self.current_time - self.interval
        for event_list in [[]] + self.events[:timesteps_ahead]:
            cur_time += self.interval
            for event in event_list:
                if type(event) is events.LocalEnergyGeneration:
                    if event.grid_connector_id != gc_id:
                        continue
                    cur_loads[event.name] = -event.value
                elif type(event) is events.FixedLoad:
                    if event.grid_connector_id != gc_id:
                        continue
                    cur_loads[event.name] = event.value
                elif type(event) is events.GridOperatorSignal:
                    if event.grid_connector_id != gc_id or event.max_power is None:
                        continue
                    cur_max_power = event.max_power
                    # vehicle events ignored (use vehicle info such as estimated_time_of_departure)

            # save information for each timestep
            timesteps.append(
                {
                    "power": sum(cur_loads.values()),
                    "max_power": cur_max_power,
                    "window": util.datetime_within_time_window(
                        cur_time, self.time_windows[gc.grid_operator], gc.voltage_level)
                }
            )

        gc.window = util.datetime_within_time_window(
            self.current_time, self.time_windows[gc.grid_operator], gc.voltage_level)
        peak_power = self.peak_power[gc_id]

        # sort vehicles by length of standing time
        # (in case out-of-window-charging is not enough, use peak shaving)
        vehicles = {k: v for k, v in sorted(vehicles.items(),
                    key=lambda t: t[1].estimated_time_of_departure or self.current_time)}
        for v_id, vehicle in vehicles.items():
            cs_id = vehicle.connected_charging_station
            cs = self.world_state.charging_stations[cs_id]
            vehicle.schedule = 0  # planned power for actual current timestep (idx=0)
            old_soc = vehicle.battery.soc
            departure = vehicle.estimated_time_of_departure
            if departure is None or departure <= self.current_time:
                # should have left already: assume next timestep as departure
                departure = self.current_time + self.interval
            depart_idx = -((departure - self.current_time) // -self.interval)
            connected_ts = timesteps[:depart_idx]

            def charge_vehicle(power, ts_info):
                p = util.clamp_power(power, vehicle, cs)
                p = min(p, ts_info["max_power"] - ts_info["power"])
                avg_power = vehicle.battery.load(self.interval, target_power=p)["avg_power"]
                return p, avg_power

            power_levels = [0] * depart_idx
            # try to charge balanced outside of load windows
            num_outside_ts = sum([not ts["window"] for ts in connected_ts])
            for ts_idx, ts in enumerate(connected_ts):
                if not ts["window"]:
                    # distribute power evenly over remaining standing time
                    power = vehicle.get_energy_needed() * ts_per_hour / num_outside_ts
                    # scale with efficiency, as this is what actually affects the SoC
                    power /= vehicle.battery.efficiency
                    power, avg_power = charge_vehicle(power, ts)
                    power_levels[ts_idx] = avg_power
                    num_outside_ts -= 1
                    if ts_idx == 0:
                        vehicle.schedule = power

            needs_charging = vehicle.desired_soc - vehicle.battery.soc > self.EPS

            # not enough: peak shaving within load windows (greedy up to peak power)
            if needs_charging:
                vehicle.battery.soc = old_soc
                for ts_idx, ts in enumerate(connected_ts):
                    if not ts["window"]:
                        # apply balanced charging
                        power_levels[ts_idx] = vehicle.battery.load(
                            self.interval, target_power=power_levels[ts_idx])["avg_power"]
                    else:
                        # use up to peak power, but not more than needed
                        power = min(
                            vehicle.get_energy_needed() * ts_per_hour / vehicle.battery.efficiency,
                            peak_power - ts["power"])
                        p, power_levels[ts_idx] = charge_vehicle(power, ts)
                        if ts_idx == 0:
                            vehicle.schedule = p
                # greedy might not have been enough, need to increase peak load
                # => fall back to peak shaving
                needs_charging = vehicle.desired_soc - vehicle.battery.soc > self.EPS

            if needs_charging:
                # find optimum power level through binary search
                min_power = min([ts["power"] for ts in connected_ts])
                max_power = max([ts["max_power"] for ts in connected_ts])
                power_levels_copy = deepcopy(power_levels)
                while max_power - min_power > self.EPS:
                    vehicle.battery.soc = old_soc
                    target_power = (max_power + min_power) / 2
                    for ts_idx, ts in enumerate(connected_ts):
                        if not ts["window"]:
                            # apply balanced charging
                            # might change (different part of charging curve),
                            # but more important to target balanced power
                            power_levels[ts_idx] = vehicle.battery.load(
                                self.interval, target_power=power_levels_copy[ts_idx])["avg_power"]
                        else:
                            # load window: get difference between opt power and current power
                            power = max(target_power - ts["power"], 0)
                            p, power_levels[ts_idx] = charge_vehicle(power, ts)
                            if ts_idx == 0:
                                vehicle.schedule = p
                    if vehicle.desired_soc - vehicle.battery.soc < self.EPS:
                        # charged enough: decrease power
                        max_power = target_power
                    else:
                        # not charged enough: increase power
                        min_power = target_power

            # add power levels to gc info
            for ts_idx, ts_info in enumerate(connected_ts):
                ts_info["power"] += power_levels[ts_idx]
                # adjust peak power prognosis (might be reduced with batteries)
                if ts_info["window"] and ts_info["power"] - peak_power > self.EPS:
                    peak_power = ts_info["power"]

            # revert soc to prepare real charging (after surplus considerations)
            vehicle.battery.soc = old_soc

        # use surplus power to charge above desired soc
        for vehicle in vehicles.values():
            vehicle.schedule -= min(timesteps[0]["power"], 0)
            if vehicle.schedule > 0:
                cs_id = vehicle.connected_charging_station
                p = vehicle.battery.load(self.interval, target_power=vehicle.schedule)["avg_power"]
                charging_stations[cs_id] = p
                gc.add_load(cs_id, p)

        bat_info = dict()
        gc_loads = gc.current_loads.copy()

        # (dis)charging commands for stationary batteries
        for b_id, battery in stationary_batteries.items():
            bat_info[b_id] = {"soc": battery.soc, "power": 0}
            if gc.window:
                # charge when below peak load, discharge when above
                power = sum(gc_loads.values()) - self.peak_power[gc_id]
                if power >= battery.min_charging_power:
                    # current load above peak power within window: discharge
                    bat_info[b_id]["power"] = -power
                    power = battery.unload(self.interval, target_power=power)["avg_power"]
                    gc_loads[b_id] = -power
                elif power <= -battery.min_charging_power:
                    # current load below peak power: charge up to peak power
                    bat_info[b_id]["power"] = -power
                    power = battery.load(self.interval, target_power=-power)["avg_power"]
                    gc_loads[b_id] = power
            else:
                # outside of window: charge balanced until window change
                # only current timestep computed, no look-ahead
                energy_needed = (1 - battery.soc) * battery.capacity
                p = energy_needed * ts_per_hour / battery.efficiency / ts_until_window_change
                p2 = min(gc.max_power - sum(gc_loads.values()), p)
                p3 = 0
                if p2 >= battery.min_charging_power:
                    bat_info[b_id]["power"] = p2
                    p3 = battery.load(self.interval, target_power=p2)["avg_power"]
                    gc_loads[b_id] = p3

        # store surplus power in batteries, apply commands
        for b_id, battery in stationary_batteries.items():
            # revert to original soc
            battery.soc = bat_info[b_id]["soc"]
            power = bat_info[b_id]["power"]
            if power >= 0:
                # idle or charging: use surplus
                power -= min(sum(gc_loads.values()), 0)
                if power >= battery.min_charging_power:
                    power = battery.load(self.interval, target_power=power)["avg_power"]
                    gc.add_load(b_id, power)
                    gc_loads[b_id] = power
            else:
                # discharging: just do it
                power = battery.unload(self.interval, target_power=-power)["avg_power"]
                gc.add_load(b_id, -power)
                gc_loads[b_id] = -power

        # might have to increase peak power when within window
        if gc.window:
            self.peak_power[gc_id] = max(self.peak_power[gc_id], gc.get_current_load())

        return charging_stations
