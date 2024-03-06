from copy import deepcopy
import datetime as dt
from warnings import warn

from spice_ev import util, events
from spice_ev.strategy import Strategy


class PeakShaving(Strategy):
    """
    Balance out and minimize grid connector power over time.
    """
    def __init__(self, components, start_time, **kwargs):
        self.HORIZON = 24  # look ahead for GC events in hours
        self.perfect_foresight = True  # perfect foresight for grid situation and vehicle events
        super().__init__(components, start_time, **kwargs)
        self.HORIZON = dt.timedelta(hours=self.HORIZON)
        self.description = "Peak Shaving"

        if self.perfect_foresight:
            all_events = self.events.vehicle_events + self.events.grid_operator_signals
            for name, load_list in self.events.fixed_load_lists.items():
                all_events.extend(load_list.get_events(name, events.FixedLoad))
            for name, local_generation in self.events.local_generation_lists.items():
                all_events.extend(local_generation.get_events(name, events.LocalEnergyGeneration))

            # make all events known at least HORIZON hours in advance
            changed = 0
            for event in all_events:
                old_signal_time = event.signal_time
                event.signal_time = min(event.signal_time, event.start_time - self.HORIZON)
                # make sure events don't signal before start
                event.signal_time = max(event.signal_time, start_time)
                changed += event.signal_time < old_signal_time
            if changed:
                print(changed, "events signaled earlier")
            self.events = sorted(all_events, key=lambda ev: ev.start_time)

        # check vehicle types: constant charging curve expected
        for name, vtype in components.vehicle_types.items():
            power_set = set([p[1] for p in vtype.charging_curve.points])
            if len(power_set) > 1:
                warn(f"Vehicle type {name} has non-constant charging curve, "
                     "results may be sub-optimal", stacklevel=100)

    def step(self):
        """Calculates charging power in each timestep.

        :return: current time and commands of the charging stations
        :rtype: dict
        """
        charging_stations = {}
        for gc_id, gc in self.world_state.grid_connectors.items():
            charging_stations.update(self.step_gc(gc_id, gc))
        return {'current_time': self.current_time, 'commands': charging_stations}

    def step_gc(self, gc_id, gc):
        # ---------- GET NEXT EVENTS ---------- #
        timesteps = []

        # look ahead (limited by horizon)
        # get future events and predict fixed load and cost for each timestep
        event_idx = 0
        timesteps_ahead = int(self.HORIZON / self.interval)

        sim_vehicles = deepcopy(self.world_state.vehicles)
        vehicles_present = {}
        vehicle_arrivals = []
        for vid, v in sim_vehicles.items():
            cs_id = v.connected_charging_station
            if cs_id is None:
                continue
            cs = self.world_state.charging_stations[cs_id]
            if cs.parent == gc_id:
                vehicles_present[vid] = len(vehicle_arrivals)
                depart_idx = None
                if v.estimated_time_of_departure is not None:
                    delta_t = v.estimated_time_of_departure - self.current_time
                    depart_idx = -(-delta_t // self.interval)
                vehicle_arrivals.append({
                    "vid": vid,
                    "vehicle": deepcopy(v),
                    "arrival_idx": 0,
                    "depart_idx": depart_idx  # might be overwritten by departure event
                })

        gc_info = {
            "vehicles": vehicles_present,
            "max_power": gc.cur_max_power,
            "cur_power": gc.get_current_load(),
            "fixed_load": gc.get_current_load(),
        }
        cur_loads = deepcopy(gc.current_loads)
        cur_time = self.current_time - self.interval
        # perfect foresight: remove past events from event list
        while self.perfect_foresight and len(self.events) > 0:
            if self.events[0].start_time <= self.current_time:
                self.events.pop(0)
            else:
                break

        for timestep_idx in range(timesteps_ahead):
            cur_time += self.interval

            # peek into future events
            while True:
                try:
                    if self.perfect_foresight:
                        event = self.events[event_idx]
                    else:
                        event = self.world_state.future_events[event_idx]
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
                    gc_info["max_power"] = event.max_power
                elif type(event) is events.VehicleEvent:
                    if event.event_type == "departure":
                        if event.vehicle_id in gc_info["vehicles"]:
                            v_idx = gc_info["vehicles"].pop(event.vehicle_id)
                            vehicle_arrivals[v_idx]["depart_idx"] = timestep_idx
                        # perfect charge (up to desired soc if below battery soc)
                        sim_vehicles[event.vehicle_id].battery.soc = max(
                            sim_vehicles[event.vehicle_id].battery.soc,
                            sim_vehicles[event.vehicle_id].desired_soc)
                    else:
                        # arrival
                        cs_id = event.update.get("connected_charging_station")
                        if cs_id is None:
                            continue
                        # update vehicle info
                        vid = event.vehicle_id
                        vehicle = sim_vehicles[vid]
                        vehicle.desired_soc = event.update["desired_soc"]
                        vehicle.battery.soc += event.update["soc_delta"]
                        vehicle.estimated_time_of_departure = event.update[
                            "estimated_time_of_departure"]
                        vehicle.connected_charging_station = cs_id
                        cs = self.world_state.charging_stations[cs_id]
                        if cs.parent == gc_id:
                            assert vid not in gc_info["vehicles"], (
                                f"{vid} already standing at {event.start_time} "
                                f"({gc_info['vehicles'][vid]} / {timestep_idx})")
                            gc_info["vehicles"][vid] = len(vehicle_arrivals)
                            depart_idx = None
                            if vehicle.estimated_time_of_departure is not None:
                                delta_t = vehicle.estimated_time_of_departure - self.current_time
                                depart_idx = -(-delta_t // self.interval)
                            vehicle_arrivals.append({
                                "vid": vid,
                                "vehicle": deepcopy(vehicle),
                                "arrival_idx": timestep_idx,
                                "depart_idx": depart_idx,  # might be changed by departure event
                            })

            gc_info["cur_power"] = sum(cur_loads.values())
            gc_info["fixed_load"] = sum(cur_loads.values())
            timesteps.append(deepcopy(gc_info))

        charging_stations = {}

        # no depart_idx: ignore vehicle
        vehicles = [v for v in vehicle_arrivals if v["depart_idx"] is not None]

        # order vehicles by standing time => charge those with the least standing time first
        vehicles = sorted(vehicles,
                          key=lambda v: min(v["depart_idx"], timesteps_ahead) - v["arrival_idx"])

        # --- ADJUST POWER CURVE --- #
        for v_info in vehicles:
            # get arrival/departure
            arrival_idx = v_info["arrival_idx"]
            depart_idx = v_info["depart_idx"]
            sim_vehicle = v_info["vehicle"]
            energy_needed = sim_vehicle.get_energy_needed()
            if arrival_idx >= depart_idx:
                # faulty arrival/departure: default power needed (instant departure)
                cs = self.world_state.charging_stations.get(sim_vehicle.connected_charging_station)
                power = energy_needed * self.ts_per_hour / sim_vehicle.battery.efficiency
                power = min(power, gc.cur_max_power - timesteps[0]["cur_power"])
                power = util.clamp_power(power, sim_vehicle, cs)
                sim_vehicle.schedule = power
                timesteps[0]["cur_power"] += power
                continue

            # scale energy needed with remaining standing time
            if depart_idx > timesteps_ahead:
                f = (timesteps_ahead - arrival_idx) / (depart_idx - arrival_idx)
                energy_needed *= f
                desired_soc = sim_vehicle.battery.soc + (
                    f*(sim_vehicle.desired_soc - sim_vehicle.battery.soc))
                sim_vehicle.desired_soc = desired_soc
                depart_idx = timesteps_ahead
                v_info["depart_idx"] = depart_idx
            v_info["energy_needed"] = energy_needed

            # apply charging strategy
            sim_vehicle.schedule = self.fast_charge(v_info, timesteps)

        # use surplus for all vehicles currently at charging station, apply power
        for v_info in vehicles:
            if v_info["arrival_idx"] > 0:
                continue
            sim_vehicle = v_info["vehicle"]
            sim_vehicle.schedule -= min(timesteps[0]["cur_power"], 0)
            if sim_vehicle.schedule > 0:
                cs_id = sim_vehicle.connected_charging_station
                avg_power = self.world_state.vehicles[v_info["vid"]].battery.load(
                    self.interval, target_power=sim_vehicle.schedule)["avg_power"]
                charging_stations[cs_id] = gc.add_load(cs_id, avg_power)

        # use batteries to balance power levels
        for b_id, battery in self.world_state.batteries.items():
            if battery.parent != gc_id:
                continue
            timesteps[0]["cur_power"] = gc.get_current_load()

            # binary search for optimum power level
            # max: highest peak within horizon
            # min: full battery discharge at highest peak
            # charge when below target power, discharge when above
            # increase target power when there is any load above
            # decrease target power if all loads are at or below
            # problem: closer to horizon, charging prediction becomes flawed (may still change)
            # => reduce influence of charging prediction the further it is from now
            # formula: 100% accurate until 2/3 from now, 0% accurate at horizon, linear in-between
            power_levels = [0]*timesteps_ahead
            for i, ts in enumerate(timesteps):
                f = min(1, 3*(1-i/timesteps_ahead))
                power_levels[i] = f*ts["cur_power"] + (1-f)*ts["fixed_load"]
            max_power = max(power_levels + [0])
            min_power = battery.unloading_curve.max_power * battery.efficiency
            min_power = max(max_power - min_power, 0)
            power = [0] * timesteps_ahead  # future battery load, updated when target changes
            old_soc = battery.soc
            cur_power = max(-power_levels[0], 0)  # default (needed if power is negative)
            target_power = 0  # default (needed if power is negative)
            while max_power - min_power > self.EPS:
                target_power = (min_power + max_power) / 2
                for ts_idx, pl in enumerate(power_levels):
                    delta_power = target_power - pl
                    p = 0  # battery power
                    if delta_power >= battery.min_charging_power:
                        # below target: charge
                        p = battery.load(self.interval, target_power=delta_power)["avg_power"]
                        if ts_idx == 0:
                            cur_power = delta_power
                    elif delta_power <= -battery.min_charging_power:
                        # above target: discharge
                        p = -battery.unload(self.interval, target_power=-delta_power)["avg_power"]
                        if ts_idx == 0:
                            cur_power = delta_power
                    power[ts_idx] = p
                    if pl + p - target_power > self.EPS:
                        # fail (at least one timestep above target power): increase
                        min_power = target_power
                        break
                else:
                    # all timesteps at or below target power: decrease
                    max_power = target_power
                battery.soc = old_soc

            # found optimal level. Where can battery charge, so that peaks are decreased?
            # find last peak above target level (timesteps after last peak are ignored)
            for i, pl in enumerate(reversed(power_levels)):
                if pl > target_power:
                    break
            else:
                i += 1  # all timesteps below target
            last_peak_idx = timesteps_ahead - i

            # max_power = min_power = target_power, remains
            # min power: minimum power level or target power, but not negative
            # as max_power is never negative as well, the avg of the two is also not negative
            min_power = min(power_levels[:last_peak_idx + 1] + [target_power])
            min_power = max(min_power, 0)
            while max_power - min_power > self.EPS:
                cur_power = 0
                # charge limit: new limit for charging,
                # but previous target power must still be reached when discharging
                charge_limit = (min_power + max_power) / 2
                # only simulate until last peak (after that only charging events)
                for ts_idx, pl in enumerate(power_levels[:last_peak_idx+1]):
                    delta = charge_limit - pl
                    p = 0
                    if delta >= battery.min_charging_power:
                        p = battery.load(self.interval, target_power=delta)["avg_power"]
                        if ts_idx == 0:
                            cur_power = delta
                    elif power[ts_idx] < 0:
                        p = -battery.unload(self.interval, target_power=-power[ts_idx])["avg_power"]
                        if ts_idx == 0:
                            cur_power = p
                    if pl + p - target_power > self.EPS:
                        # target could not be matched with reduced charge level -> increase
                        min_power = charge_limit
                        break
                else:
                    # all peaks could be reduced to target level -> decrease charge level
                    max_power = charge_limit
                battery.soc = old_soc

            # converged -> apply power
            if cur_power < 0:
                p = -battery.unload(self.interval, target_power=-cur_power)["avg_power"]
            else:
                p = battery.load(self.interval, target_power=cur_power)["avg_power"]
            gc.add_load(b_id, p)  # p is always set
            # TODO: keep track of future simulated power level changes for other batteries
        return charging_stations

    def fast_charge(self, v_info, timesteps):
        sim_vehicle = v_info["vehicle"]
        arrival_idx = v_info["arrival_idx"]
        depart_idx = v_info["depart_idx"]
        energy_needed = v_info["energy_needed"]
        if energy_needed <= self.EPS:
            return 0
        cs = self.world_state.charging_stations[sim_vehicle.connected_charging_station]

        # get power over standing time, sort ascending
        power_levels = [(timesteps[i]["cur_power"], i) for i in range(arrival_idx, depart_idx)]
        power_levels = sorted(power_levels)

        # find timesteps with same power level
        idx = 0
        prev_power = power_levels[0][0]
        prev_energy = 0
        power = 0
        eff = sim_vehicle.battery.efficiency
        while idx < len(power_levels) and energy_needed - prev_energy > self.EPS:
            if power_levels[idx][0] - prev_power < self.EPS:
                idx += 1
                continue
            # power levels differ: try to fill up difference
            energy = 0
            power = power_levels[idx][0]
            for info in timesteps[arrival_idx:depart_idx]:
                p = min(power, info["max_power"])  # don't exceed current max power
                # assumption: charging curve constant, can't exceed maximum
                p = min(p, sim_vehicle.battery.loading_curve.max_power, cs.max_power)
                p = max(p - info["cur_power"], 0)  # cur_power higher: no power
                energy += p
            energy /= self.ts_per_hour / eff

            if energy - energy_needed > self.EPS:
                # compute fraction of energy needed
                frac = 1 - (energy - energy_needed) / (energy - prev_energy)
                power = prev_power + frac * (power - prev_power)
                prev_energy = energy_needed
                break

            prev_power = power
            prev_energy = energy
        if energy_needed - prev_energy > self.EPS:
            # energy need not satisfied yet: must exceed highest power peak
            # distribute evenly over timesteps (ignore power restrictions)
            power = prev_power + (energy_needed - prev_energy) * self.ts_per_hour / idx / eff

        opt_power = power
        # charge (in order of timesteps)
        delta = 0
        command = 0
        for pl in sorted(power_levels[:idx], key=lambda x: x[1]):
            power = min(opt_power + delta, timesteps[pl[1]]["max_power"]) - pl[0]
            power = util.clamp_power(power, sim_vehicle, cs)
            avg_power = sim_vehicle.battery.load(self.interval, target_power=power)["avg_power"]
            timesteps[pl[1]]["cur_power"] += avg_power
            delta += power - avg_power
            if pl[1] == 0:
                command = power
        return command
