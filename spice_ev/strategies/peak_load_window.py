from copy import deepcopy
import datetime
import json
import warnings

from spice_ev import events, util
from spice_ev.strategy import Strategy


class PeakLoadWindow(Strategy):
    """ Charging strategy that prioritizes times outside of high load time windows.

    Charge balanced outside of windows. Inside time windows different sub-strategies are possible.
    """
    def __init__(self, components, start_time, **kwargs):
        self.time_windows = None
        self.LOAD_STRAT = "greedy"  # peak_shaving or greedy
        super().__init__(components, start_time, **kwargs)
        self.description = "§19.2 StromNEV"
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

        for gc_id, gc in self.world_state.grid_connectors.items():
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
        self.events = sorted(local_events, key=lambda ev: ev.start_time)

        # find highest peak of GC power within time windows
        current_loads = {}
        peak_power = {}
        for gc_id, gc in self.world_state.grid_connectors.items():
            current_loads[gc_id] = deepcopy(gc.current_loads)
            peak_power[gc_id] = 0
        cur_time = start_time - self.interval
        event_idx = 0
        for i in range(self.n_intervals):
            cur_time += self.interval
            try:
                event = self.events[event_idx]
            except IndexError:
                # no more events
                break
            if event.start_time > cur_time:
                # not this timestep
                break
            event_idx += 1
            gc_id = event.grid_connector_id
            gc = self.world_state.grid_connectors[gc_id]
            if type(event) is events.LocalEnergyGeneration:
                current_loads[gc_id][event.name] = -event.value
            elif type(event) is events.FixedLoad:
                current_loads[gc_id][event.name] = event.value
            is_window = util.datetime_within_power_level_window(
                cur_time, self.time_windows[gc.grid_operator], gc.voltage_level)
            if is_window:
                peak_power[gc_id] = max(peak_power[gc_id], sum(current_loads[gc_id].values()))

        self.peak_power = peak_power

    def step(self):
        """ Calculate charging power in each timestep.

        :return: current time and commands of the charging stations
        :rtype: dict
        """
        commands = dict()
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
            cs = self.world_state.charging_stations[cs_id]
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
        event_idx = 0
        cur_time = self.current_time - self.interval
        cur_loads = deepcopy(gc.current_loads)
        cur_max_power = gc.cur_max_power

        for timestep_idx in range(timesteps_ahead):
            cur_time += self.interval
            # peek into future events
            while True:
                try:
                    event = self.events[event_idx]
                except IndexError:
                    # no more events
                    break
                if event.start_time > cur_time:
                    # not this timestep
                    break
                event_idx += 1
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
                    "window": util.datetime_within_power_level_window(
                        cur_time, self.time_windows[gc.grid_operator], gc.voltage_level)
                }
            )

        gc.window = util.datetime_within_power_level_window(
            self.current_time, self.time_windows[gc.grid_operator], gc.voltage_level)

        # sort vehicles by length of standing time
        # (in case out-of-window-charging is not enough, use peak shaving)
        vehicles = {k: v for k, v in sorted(vehicles.items(),
                    key=lambda t: t[1].estimated_time_of_departure or self.current_time)}
        for v_id, vehicle in vehicles.items():
            cs_id = vehicle.connected_charging_station
            cs = self.world_state.charging_stations[cs_id]
            cur_power = 0  # power for actual current timestep (idx=0)
            old_soc = vehicle.battery.soc
            departure = vehicle.estimated_time_of_departure
            depart_idx = -((departure - self.current_time) // -self.interval)
            connected_ts = timesteps[:depart_idx]

            def charge_vehicle(power, ts_info):
                p = util.clamp_power(power, vehicle, cs)
                p = min(p, ts_info["max_power"] - ts_info["power"])
                avg_power = vehicle.battery.load(self.interval, target_power=p)["avg_power"]
                return p, avg_power

            power_levels = [0]*depart_idx
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
                        cur_power = power

            needs_charging = vehicle.desired_soc - vehicle.battery.soc > self.EPS

            # take note of soc after out-of-window charging
            intermediate_soc = vehicle.battery.soc
            # not enough: peak shaving within load windows
            if needs_charging and self.LOAD_STRAT == "greedy":
                for ts_idx, ts in enumerate(connected_ts):
                    if ts["window"]:
                        # use up to peak power, but not more than needed
                        power = min(
                            vehicle.get_energy_needed() * ts_per_hour / vehicle.battery.efficiency,
                            self.peak_power[gc_id] - ts["power"])
                        p, power_levels[ts_idx] = charge_vehicle(power, ts)
                        if ts_idx == 0:
                            cur_power = p
                # greedy might not have been enough, need to increase peak load
                # => fall back to peak shaving
                needs_charging = vehicle.desired_soc - vehicle.battery.soc > self.EPS
            if needs_charging:
                # find optimum power level through binary search
                min_power = min([ts["power"] for ts in connected_ts])
                max_power = max([ts["max_power"] for ts in connected_ts])
                while max_power - min_power > self.EPS:
                    vehicle.battery.soc = intermediate_soc
                    target_power = (max_power + min_power) / 2
                    for ts_idx, ts in enumerate(connected_ts):
                        if ts["window"]:
                            # load window: get difference between opt power and current power
                            power = max(target_power - ts["power"], 0)
                            p, power_levels[ts_idx] = charge_vehicle(power, ts)
                            if ts_idx == 0:
                                cur_power = p
                    if vehicle.desired_soc - vehicle.battery.soc < self.EPS:
                        # charged enough: decrease power
                        max_power = target_power
                    else:
                        # not charged enough: increase power
                        min_power = target_power

            # add power levels to gc info
            for ts_idx, ts_info in enumerate(connected_ts):
                ts_info["power"] += power_levels[ts_idx]
                # might have to increase global max power
                if ts_info["window"] and ts_info["power"] > self.peak_power[gc_id]:
                    # warnings.warn(f"{gc_id}: increase peak power to {ts_info["power"]}")
                    self.peak_power[gc_id] = ts_info["power"]

            # --- charge for real --- #
            vehicle.battery.soc = old_soc
            if cur_power:
                power = vehicle.battery.load(self.interval, target_power=cur_power)["avg_power"]
                charging_stations[cs_id] = power
                gc.add_load(cs_id, power)

        return charging_stations
