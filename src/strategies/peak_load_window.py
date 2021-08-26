from copy import deepcopy
import datetime

from src import events, util
from src.strategy import Strategy


class PeakLoadWindow(Strategy):
    """
    Charging strategy that prioritizes times outside of high load time windows.

    Charge balanced outside of windows, inside different substrategies are possible.
    """
    def __init__(self, constants, start_time, **kwargs):
        # defaults, can be overridden by CLO (through kwargs)

        # minimum binary seach depth
        self.ITERATIONS = 12
        self.LOAD_STRAT = 'needy'  # greedy, needy, balanced

        # init parent class Strategy. May override defaults
        super().__init__(constants, start_time, **kwargs)
        self.description = "§19stromNEV ({})".format(self.LOAD_STRAT)

        # set order of vehicles to load
        if self.LOAD_STRAT == 'greedy':
            self.sort_key = lambda v: (v.estimated_time_of_departure)
        elif self.LOAD_STRAT == 'needy':
            self.sort_key = lambda v: -v.get_energy_needed()
        elif self.LOAD_STRAT == 'balanced':
            self.sort_key = lambda v: v.get_energy_needed(full=False)
        else:
            raise NotImplementedError(self.LOAD_STRAT)

        # define time windows where drawing from grid is discouraged
        year = start_time.year
        self.time_windows = {
            # don't care about spring and summer
            "autumn": {
                # 01.09. - 30.11., 16:30 - 20:00
                "start": datetime.datetime(day=1, month=9, year=year, hour=16, minute=30),
                "end": datetime.datetime(day=30, month=11, year=year, hour=20, minute=0),
            },
            # split winter (year changes)
            "winter1": {
                # 01.12. - 31.12., 16:30 - 19:30
                "start": datetime.datetime(day=1, month=12, year=year, hour=16, minute=30),
                "end": datetime.datetime(day=31, month=12, year=year, hour=19, minute=30),
            },
            "winter2": {
                # 01.01. - 28.02., 16:30 - 19:30
                "start": datetime.datetime(day=1, month=1, year=year, hour=16, minute=30),
                "end": datetime.datetime(day=28, month=2, year=year, hour=19, minute=30),
            }
        }

        assert len(self.world_state.grid_connectors) == 1, "Only one grid connector supported"

    def step(self, event_list=[]):
        super().step(event_list)

        timesteps_per_day = datetime.timedelta(days=1) // self.interval
        timesteps_per_hour = datetime.timedelta(hours=1) / self.interval

        gc = list(self.world_state.grid_connectors.values())[0]

        ts = [{
            "window": util.datetime_within_window(self.current_time, self.time_windows),
            "loads": {k: v for k, v in gc.current_loads.items()},
        }]

        # get all vehicles that are still charging
        vehicles = {}
        standing = {}
        energy_needed = 0
        for vid, v in self.world_state.vehicles.items():
            if v.connected_charging_station is not None:
                vehicles[vid] = v
                energy_needed += v.get_energy_needed(full=False)
                standing[vid] = 0
                if v.estimated_time_of_departure is not None:
                    # how many timesteps left until leaving?
                    standing_time = v.estimated_time_of_departure - self.current_time
                    v.dep_ts = -(-standing_time // self.interval)
                else:
                    # no departure time given: assume whole day
                    v.dep_ts = timesteps_per_day
        sum_loads = sum(gc.current_loads.values())

        # peek into future events for external loads, feed-in and schedule
        event_idx = 0
        cur_time = self.current_time - self.interval
        # look one day ahead

        for timestep_idx in range(timesteps_per_day):
            cur_time += self.interval

            if timestep_idx > 0:
                # copy last GC info
                ts.append(deepcopy(ts[-1]))
                ts[-1]["window"] = util.datetime_within_window(cur_time, self.time_windows)

            # get standing times for each charging vehicle
            for vid, vehicle in vehicles.items():
                if vehicle.estimated_time_of_departure < cur_time:
                    standing[vid] += 1

            # peek into future events for external load or cost changes
            while True:
                try:
                    event = self.world_state.future_events[event_idx]
                except IndexError:
                    # no more events
                    break
                if event.start_time > cur_time:
                    # not this timestep
                    break
                # event handled: don't handle again, so increase index
                event_idx += 1
                if type(event) in [events.ExternalLoad, events.EnergyFeedIn]:
                    ts[-1]["loads"][event.name] = event.value
            # end of useful events

        safe = False
        target = 0
        max_target = gc.max_power
        min_target = -gc.max_power
        sim_vehicles = deepcopy(vehicles)
        sim_batteries = deepcopy(self.world_state.batteries)
        outside_windows = [not info["window"] for info in ts]

        while (not safe and not target) or (target and (max_target - min_target) > self.EPS):
            target = (min_target + max_target) / 2
            cur_time = self.current_time - self.interval

            # reset SoC
            for vid, v in sim_vehicles.items():
                v.battery.soc = vehicles[vid].battery.soc
                assert vid in v.connected_charging_station
            for bid, b in sim_batteries.items():
                b.soc = self.world_state.batteries[bid].soc

            # simulate future timesteps
            for ts_idx, ts_info in enumerate(ts):
                cur_time += self.interval
                cur_loads = sum(ts_info["loads"].values())
                safe = True
                sim_charging_vehicles = []
                sim_energy_needed = 0
                # check if desired SoC is met when leaving or take note of energy needed
                for v in sim_vehicles.values():
                    if v.dep_ts <= ts_idx:
                        # vehicle left: check if charged enough
                        safe &= v.get_delta_soc() < self.EPS
                    else:
                        sim_charging_vehicles.append(v)
                        sim_energy_needed += v.get_energy_needed(full=False)

                if not safe:
                    # at least one vehicle not sufficiently charged
                    break

                if ts_info["window"]:
                    # draw as little power as possible -> try to reach target
                    power = target - cur_loads
                    info = self.distribute_power(sim_charging_vehicles, power, sim_energy_needed)
                    power -= sum(info.values())
                    # support with batteries to reach target
                    for battery in sim_batteries.values():
                        if power > 0:
                            power -= battery.load(self.interval, power)["avg_power"]
                        else:
                            power += battery.unload(self.interval, -power)["avg_power"]
                    continue

                # outside of window: charge balanced
                # distribute energy over remaining standing period outside laod window
                gc_power = max(gc.max_power - cur_loads, 0)
                for v in sim_charging_vehicles:
                    num_outside_ts = sum(outside_windows[ts_idx:v.dep_ts])
                    # get power needed to reach desired SoC
                    power = v.get_energy_needed(full=False) * timesteps_per_hour
                    # distribute needed power over remaining timesteps
                    power /= num_outside_ts
                    # scale with battery efficiency
                    power /= v.battery.efficiency
                    cs_id = vehicle.connected_charging_station
                    cs = self.world_state.charging_stations[cs_id]
                    # clamp power
                    power = min(util.clamp_power(power, vehicle, cs), gc_power)
                    # charging
                    power = v.battery.load(self.interval, power, v.desired_soc)["avg_power"]
                    gc_power = max(gc_power - power, 0)
                for battery in sim_batteries.values():
                    # charge batteries
                    power = battery.load(self.interval, gc_power)["avg_power"]
                    gc_power = max(gc_power - power, 0)

            if not safe:
                # vehicles not charged in time: increase target for more power
                min_target = target
            else:
                # try to lower target
                max_target = target

        # charge for real
        if ts[0]["window"]:
            gc_power = target - sum_loads
            commands = self.distribute_power(vehicles.values(), gc_power, energy_needed)
            gc_power -= sum(commands.values())
            # support with batteries to reach target
            for bid, battery in self.world_state.batteries.items():
                if gc_power > 0:
                    power = battery.load(self.interval, gc_power)["avg_power"]
                else:
                    power = -battery.unload(self.interval, -gc_power)["avg_power"]
                gc_power -= power
                gc.add_load(bid, power)
        else:
            # outside of window: draw power
            commands = {}
            gc_power = max(gc.max_power - sum_loads, 0)
            for v in vehicles.values():
                num_outside_ts = sum(outside_windows[:v.dep_ts])
                # get power needed to reach desired SoC
                power = v.get_energy_needed(full=False) * timesteps_per_hour
                # distribute power over estimated remaining standing time
                power /= num_outside_ts
                # scale with battery efficiency
                power /= v.battery.efficiency
                cs_id = v.connected_charging_station
                cs = self.world_state.charging_stations[cs_id]
                power = min(gc_power, util.clamp_power(power, v, cs))
                # charging
                power = v.battery.load(self.interval, power, v.desired_soc)["avg_power"]
                commands[cs_id] = power
                gc_power = max(gc_power - power, 0)
            # charge batteries
            for bid, battery in self.world_state.batteries.items():
                power = battery.load(self.interval, gc_power)["avg_power"]
                gc_power = min(gc_power - power, 0)
                gc.add_load(bid, power)
        # update GC loads
        for cs_id, avg_power in commands.items():
            gc.add_load(cs_id, avg_power)

        return {'current_time': self.current_time, 'commands': commands}

    def distribute_power(self, vehicles, total_power, energy_needed):
        # distribute total_power to vehicles in iterable vehicles according to self.LOAD_STRAT
        # energy_needed is total energy needed to charge every vehicle, used in needy strategy
        if not vehicles:
            return {}
        if total_power < self.EPS:
            return {}
        if energy_needed < self.EPS:
            return {}

        commands = {}
        available_power = total_power
        vehicles_to_charge = len(vehicles)

        for vehicle in sorted(vehicles, key=self.sort_key):
            if self.LOAD_STRAT == "greedy":
                # use maximum of given power
                power = available_power
            elif self.LOAD_STRAT == "needy":
                # get fraction of precalculated power need to overall power need
                vehicle_energy_needed = vehicle.get_energy_needed(full=False)
                frac = vehicle_energy_needed / energy_needed if energy_needed > self.EPS else 0
                power = available_power * frac
                energy_needed -= vehicle_energy_needed
            elif self.LOAD_STRAT == "balanced":
                # distribute total power over vehicles
                power = available_power / vehicles_to_charge

            cs_id = vehicle.connected_charging_station
            cs = self.world_state.charging_stations[cs_id]
            power = util.clamp_power(power, vehicle, cs)
            # charging
            avg_power = vehicle.battery.load(self.interval, power, vehicle.desired_soc)["avg_power"]
            commands[cs_id] = avg_power
            available_power -= avg_power
            vehicles_to_charge -= 1
        return commands
