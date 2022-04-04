from copy import deepcopy
from datetime import timedelta

import spice_ev.src.events as events
from spice_ev.src.strategy import Strategy
from spice_ev.src.util import clamp_power


def get_power_needed(vehicle):
    # calculate power needed to reach SoC (positive or zero)
    return max(vehicle.get_delta_soc(), 0) * vehicle.battery.capacity


class ScheduleForesight(Strategy):
    def __init__(self, constants, start_time, **kwargs):
        self.LOAD_STRAT = 'needy'  # greedy, needy, balanced
        super().__init__(constants, start_time, **kwargs)

        self.description = "schedule foresight ({})".format(self.LOAD_STRAT)
        if self.LOAD_STRAT == "greedy":
            # charge vehicles in need first, then by order of departure
            self.sort_key = lambda v: (
                v.battery.soc >= v.desired_soc,
                v.estimated_time_of_departure)
        elif self.LOAD_STRAT == "needy":
            # charge cars with not much power needed first, may leave more for others
            self.sort_key = lambda v: v.get_delta_soc() * v.battery.capacity
        elif self.LOAD_STRAT == "balanced":
            # only relevant if not enough power to charge all vehicles
            self.sort_key = lambda v: (
                v.battery.soc < v.desired_soc,
                v.estimated_time_of_departure)
        else:
            "Unknown charging strategy: {}".format(self.LOAD_STRAT)

        assert len(self.world_state.grid_connectors) == 1, "Only one grid connector supported"

    def step(self, event_list=[]):
        super().step(event_list)

        gc = list(self.world_state.grid_connectors.values())[0]

        vehicles = {}

        # GC info for each future timestep until all cars left
        gc_info = [{
            "charging": set(),
            "ext_load": {k: v for k, v in gc.current_loads.items() if v > 0},
            "feed_in": {k: v for k, v in gc.current_loads.items() if v <= 0},
            "target": gc.target
        }]

        # overall power needed to fully charge vehicles
        power_needed = 0

        # gather all cars that are still charging
        for vid, vehicle in self.world_state.vehicles.items():
            cs_id = vehicle.connected_charging_station
            if cs_id is None:
                continue
            vehicles[vid] = vehicle
            gc_info[0]["charging"].add(vid)
            power_needed += get_power_needed(vehicle)

        # peek into future events for external loads, feed-in and schedule
        event_idx = 0
        cur_time = self.current_time - self.interval
        timesteps_per_day = timedelta(days=1) // self.interval
        for timestep_idx in range(timesteps_per_day):
            cur_time += self.interval

            if timestep_idx > 0:
                # copy last GC info
                gc_info.append(deepcopy(gc_info[-1]))

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
                if type(event) == events.GridOperatorSignal:
                    # update GC info
                    gc_info[-1]["target"] = event.target or gc_info[-1]["target"]
                elif type(event) == events.ExternalLoad:
                    gc_info[-1]["ext_load"][event.name] = event.value
                elif type(event) == events.EnergyFeedIn:
                    gc_info[-1]["feed_in"][event.name] = event.value
                # ignore vehicle events, use vehicle data directly
            # end of useful events

            # update vehicles still charging
            charging = set()
            for vid in gc_info[-1]["charging"]:
                vehicle = vehicles[vid]
                if vehicle.estimated_time_of_departure > cur_time:
                    charging.add(vid)
            gc_info[-1]["charging"] = charging

            if len(gc_info[-1]["charging"]) == 0:
                # all vehicles left
                break

        sim_vehicles = deepcopy(vehicles)
        sim_batteries = deepcopy(self.world_state.batteries)
        deviation = 0
        # keep track of previous values so as not to run in circles
        prev_power_deltas = []
        max_dev = gc.max_power - gc.target
        min_dev = -gc.max_power - gc.target
        while min_dev <= deviation <= max_dev:
            # reset SoC
            for vid, sim_vehicle in sim_vehicles.items():
                sim_vehicle.battery.soc = vehicles[vid].battery.soc
            for bid, sim_battery in sim_batteries.items():
                sim_battery.soc = self.world_state.batteries[bid].soc

            # take note of difference between SoC needed and actual SoC after leaving
            power_delta = {}
            gc_power_delta = 0

            for ts_idx, ts_info in enumerate(gc_info):
                sim_charging = []
                sim_power_needed = 0
                for vid, sim_vehicle in sim_vehicles.items():
                    if vid in ts_info["charging"]:
                        # vehicle still charging: append
                        sim_power_needed += get_power_needed(sim_vehicle)
                        # take note of power needed to reach desired soc
                        sim_charging.append(sim_vehicle)
                    elif vid not in power_delta:
                        # vehicle left: take note of power below desired soc
                        power_delta[vid] = get_power_needed(sim_vehicle)

                base_load = sum(ts_info["ext_load"].values()) + sum(ts_info["feed_in"].values())
                # compute power available for vehicle charging
                charging_power = ts_info["target"] - base_load + deviation
                if sim_power_needed <= 0 and charging_power >= 0:
                    # no V2G, no vehicles need power: skip vehicle charging
                    charging_power = 0

                # distribute power accoring to LOAD_STRAT
                power_used = self.distribute_power(sim_charging, charging_power, sim_power_needed)
                # get difference between allocated power and power used
                gc_power_delta += charging_power - sum(power_used.values())
                # (dis)charge batteries with remaining power
                for battery in sim_batteries.values():
                    if gc_power_delta < 0:
                        info = battery.unload(self.interval, -gc_power_delta)
                        gc_power_delta += info["avg_power"]
                    else:
                        info = battery.load(self.interval, gc_power_delta)
                        gc_power_delta -= info["avg_power"]
            sum_power_delta = sum(power_delta.values())

            # adjust deviation from schedule
            if sum_power_delta > self.EPS:
                # power_delta positive: some cars not charged enough, increase deviation
                deviation += sum_power_delta / len(gc_info)
            elif abs(gc_power_delta) > self.EPS:
                # vehicles charged enough, but schedule used power is not close to schedule
                # try to approach schedule
                # check previous gc_power_deltas if this has been seen before
                running_in_circles = False
                for ppd in prev_power_deltas:
                    running_in_circles |= abs(gc_power_delta - ppd) < self.EPS
                if running_in_circles:
                    # have been here before: stop simulation
                    break
                # not been here before: adjust deviation
                prev_power_deltas.append(gc_power_delta)
                deviation -= gc_power_delta / len(gc_info)
            else:
                break

        # charge for real
        base_load = gc.get_current_load()
        charging_power = gc.target - base_load + deviation
        commands = self.distribute_power(vehicles.values(), charging_power, power_needed)
        for cs_id, avg_power in commands.items():
            gc.add_load(cs_id, avg_power)
        charging_power -= sum(commands.values())
        # charge batteries with surplus
        for bid, battery in self.world_state.batteries.items():
            if charging_power < 0:
                bat_power = -battery.unload(self.interval, -charging_power)["avg_power"]
            else:
                power = 0 if charging_power < battery.min_charging_power else charging_power
                bat_power = battery.load(self.interval, power)["avg_power"]
            gc.add_load(bid, bat_power)
            charging_power -= bat_power
        return {'current_time': self.current_time, 'commands': commands}

    def distribute_power(self, vehicles_list, total_power, power_needed):
        # distribute total_power to vehicles in iterable vehicles_list according to self.LOAD_STRAT
        # power_needed is total power needed to charge every vehicle, used in needy strategy
        vehicles_list = sorted(vehicles_list, key=self.sort_key)
        # keep track of available power, regardless of sign
        available_power = abs(total_power)
        vehicles_to_charge = len(vehicles_list)
        charging_stations = {}

        for vehicle in vehicles_list:
            if self.LOAD_STRAT == "greedy":
                # use maximum of given power
                power = available_power
            elif self.LOAD_STRAT == "needy":
                # get fraction of precalculated power need to overall power need
                vehicle_power_needed = get_power_needed(vehicle)
                frac = vehicle_power_needed / power_needed if power_needed > self.EPS else 0
                if total_power < 0:
                    # V2G: discharge fullest most
                    power = available_power * (1-frac)
                else:
                    # normal charging: charge emptiest most
                    power = available_power * frac
                power_needed -= vehicle_power_needed
            elif self.LOAD_STRAT == "balanced":
                power = available_power / vehicles_to_charge

            cs_id = vehicle.connected_charging_station
            cs = self.world_state.charging_stations[cs_id]
            power = clamp_power(power, vehicle, cs)
            if total_power < 0:
                if vehicle.vehicle_type.v2g:
                    # V2G
                    info = vehicle.battery.unload(self.interval, power)
                    avg_power = info["avg_power"]
                else:
                    # power negative, but no V2G
                    avg_power = 0
            else:
                # charging
                info = vehicle.battery.load(self.interval, power, vehicle.desired_soc)
                avg_power = info["avg_power"]
            # take care of sign of avg_power (neg. power: -1, pos. power: +1)
            charging_stations[cs_id] = ((total_power < 0) * -2 + 1) * avg_power
            available_power -= avg_power
            vehicles_to_charge -= 1

        return charging_stations
