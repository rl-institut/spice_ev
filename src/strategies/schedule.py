from copy import deepcopy
from datetime import timedelta

import src.events as events
from src.strategy import Strategy
from src.util import clamp_power, timestep_within_window, dt_to_end_of_time_window


class Schedule(Strategy):
    def __init__(self, constants, start_time, **kwargs):
        self.LOAD_STRAT = 'needy'  # greedy, balanced
        self.TS_remaining_to_charge = False
        self.ITERATIONS = 12
        super().__init__(constants, start_time, **kwargs)

        self.description = "schedule ({})".format(self.LOAD_STRAT)
        if self.LOAD_STRAT == "greedy":
            self.sort_key = lambda v: (
                v[0].battery.soc >= v[0].desired_soc,
                v[0].estimated_time_of_departure)
        elif self.LOAD_STRAT == "needy":
            # charge cars with not much power needed first, may leave more for others
            self.sort_key = lambda v: v[0].get_delta_soc() * v[0].battery.capacity
        elif self.LOAD_STRAT == "balanced":
            # only relevant if not enough power to charge all vehicles
            self.sort_key = lambda v: v[0].estimated_time_of_departure
        elif self.LOAD_STRAT == "balanced_vehicle":
            self.sort_key = None
        else:
            "Unknown charging startegy: {}".format(self.LOAD_STRAT)
    

    def charge_cars(self):
        charging_stations = {}

        vehicles_at_gc = {gc_id: [] for gc_id in self.world_state.grid_connectors.keys()}
        # find vehicles for each grid connector
        for vehicle in self.world_state.vehicles.values():
            cs_id = vehicle.connected_charging_station
            if cs_id is None:
                continue
            cs = self.world_state.charging_stations[cs_id]
            gc_id = cs.parent
            vehicles_at_gc[gc_id].append((vehicle, cs))

        for gc_id, vehicles in vehicles_at_gc.items():
            gc = self.world_state.grid_connectors[gc_id]
            assert gc.target is not None, "No schedule for GC '{}'".format(gc_id)
            vehicles = sorted(vehicles, key=self.sort_key)

            total_power = gc.target - gc.get_current_load()

            power_needed = []
            for vehicle, _ in vehicles:
                soc_needed = vehicle.desired_soc - vehicle.battery.soc
                power_needed.append(soc_needed * vehicle.battery.capacity)

            if total_power < self.EPS or sum(power_needed) < self.EPS:
                # no power scheduled or all cars fully charged: skip this GC
                continue

            if self.LOAD_STRAT == "balanced":
                # distribute power to vehicles
                # remove vehicles at capacity limit
                vehicles = [v for v in vehicles if v[0].battery.soc < 1 - self.EPS]

                # distributed power must be enough for all vehicles (check lower limit)
                # as this might not be enough, remove vehicles from queue
                # naive: distribute evenly
                safe = True
                for vehicle, cs in vehicles:
                    power = total_power / len(vehicles)
                    if clamp_power(power, vehicle, cs) == 0:
                        safe = False
                        break
                if not safe:
                    # power is not enough to charge all vehicles evenly
                    # remove vehicles with sufficient charge
                    need_charging_vehicles = []
                    for vehicle, cs in vehicles:
                        if vehicle.battery.soc < vehicle.desired_soc:
                            need_charging_vehicles.append((vehicle, cs))
                    vehicles = need_charging_vehicles
                    # try to distribute again
                    safe = True
                    for vehicle, cs in vehicles:
                        power = total_power / len(vehicles)
                        if clamp_power(power, vehicle, cs) == 0:
                            safe = False
                            break
                while not safe and len(vehicles) > 0:
                    # still not enough power to charge all vehicles in need
                    # remove vehicles one by one,
                    # beginning with those with longest remaining standing time
                    vehicles = vehicles[:-1]
                    safe = True
                    for vehicle, cs in vehicles:
                        power = total_power / len(vehicles)
                        if clamp_power(power, vehicle, cs) == 0:
                            safe = False
                            break
                # only vehicles that can really be charged remain in vehicles now

            for vehicle, cs in vehicles:
                if self.LOAD_STRAT == "greedy":
                    # charge until scheduled target is reached
                    power = gc.target - gc.get_current_load()
                elif self.LOAD_STRAT == "needy":
                    # get fraction of precalculated power need to overall power need
                    total_power_needed = sum(power_needed)
                    power_available = gc.target - gc.get_current_load()
                    if total_power_needed > self.EPS:
                        power = power_available * (power_needed.pop(0) / total_power_needed)
                elif self.LOAD_STRAT == "balanced":
                    power = total_power / len(vehicles)

                power = clamp_power(power, vehicle, cs)
                avg_power = vehicle.battery.load(self.interval, power, target_soc=vehicle.desired_soc)["avg_power"]
                cs_id = vehicle.connected_charging_station
                charging_stations[cs_id] = gc.add_load(cs_id, avg_power)

        return charging_stations


    def collect_future_gc_info(self, dt=timedelta(days=1)):

        gc = list(self.world_state.grid_connectors.values())[0]
        
        # GC info for each future timestep until all cars left
        gc_info = [{
            #"charging": set(),
            "ext_load": {k: v for k, v in gc.current_loads.items() if v > 0},
            #"feed_in": {k: v for k, v in gc.current_loads.items() if v <= 0},
            "target": gc.target
        }]
        
        # peek into future events for external loads, feed-in and schedule
        event_idx = 0
        cur_time = self.current_time - self.interval
        timesteps = dt // self.interval
        for timestep_idx in range(timesteps):
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
                # ignore vehicle events, use vehicle data directly
                # ignore feedIn for now as well
            # end of useful events peek into future events for external loads, schedule
        

        return gc_info


    def charge_cars_balanced(self):
        charging_stations = {}
        if not self.TS_remaining_to_charge:
            dt_to_end_core_standing_time = dt_to_end_of_time_window(self.current_time
                                                                   ,self.core_standing_time
                                                                   ,self.interval)

        gc_info = self.collect_future_gc_info(dt_to_end_core_standing_time)
        TS_remaining_to_charge = sum([info["target"] - sum(info["ext_load"].values()) > self.EPS for info in gc_info])

        for vehicle_id in sorted(self.world_state.vehicles.keys()):
            # get vehicle
            vehicle = self.world_state.vehicles[vehicle_id]
            cs_id = vehicle.connected_charging_station
            if cs_id is None:
                # not connected
                continue
            # get connected charging station
            cs = self.world_state.charging_stations[cs_id]
            gc = self.world_state.grid_connectors[cs.parent]
            gc_power_left = gc.target - gc.get_current_load()
            power = 0
            delta_soc = vehicle.get_delta_soc()

            if delta_soc > self.EPS:
                # get limits
                min_power = max(vehicle.vehicle_type.min_charging_power, cs.min_power)
                max_power = gc_power_left 
                max_power = min(max_power, vehicle.vehicle_type.charging_curve.max_power)
                max_power = clamp_power(max_power, vehicle, cs)
                # time until departure
                dt = TS_remaining_to_charge * self.interval
                old_soc = vehicle.battery.soc
                idx = 0
                safe = False
                # converge to optimal power for the duration
                # at least ITERATIONS cycles
                # must end with slightly too much power used
                # abort if min_power == max_power (e.g. unrealistic goal)
                while (idx < self.ITERATIONS or not safe) and max_power - min_power > self.EPS:
                    idx += 1
                    # get new power value (binary search: use average)
                    power = (max_power + min_power) / 2
                    # load whole time with same power
                    charged_soc = vehicle.battery.load(dt, power)["soc_delta"]
                    # reset SOC
                    vehicle.battery.soc = old_soc

                    if delta_soc - charged_soc > self.EPS:  # charged_soc < delta_soc
                        # power not enough
                        safe = False
                        min_power = power
                    else:  # charged_soc >= delta_soc:
                        # power too much or just right (may be possible with less power)
                        safe = True
                        max_power = power

            # load with power
            avg_power = vehicle.battery.load(self.interval, power, target_soc=vehicle.desired_soc)['avg_power']
            charging_stations[cs_id] = gc.add_load(cs_id, avg_power)


            # can active charging station bear minimum load?
            assert cs.max_power >= cs.current_power - self.EPS, (
                "{} - {} over maximum load ({} > {})".format(
                    self.current_time, cs_id, cs.current_power, cs.max_power))
            # can grid connector bear load?
            assert gc.cur_max_power >= gc.get_current_load() - self.EPS, (
                "{} - {} over maximum load ({} > {})".format(
                    self.current_time, cs.parent, gc.get_current_load(), gc.cur_max_power))

        return charging_stations


    def utilize_stationary_batteries(self):
        # adjust deviation with batteries
        for bid, battery in self.world_state.batteries.items():
            gc_id = battery.parent
            gc = self.world_state.grid_connectors[gc_id]
            if gc.target is None:
                # no schedule set
                continue
            # get difference between target and GC load
            power = gc.target - gc.get_current_load()
            if power < -self.EPS:
                # discharge
                # to provide the energy the schedule asks for, charge with more
                # power to make up for loss due to efficiency
                bat_power = -battery.unload(self.interval, -power / battery.efficiency)["avg_power"]
            elif power > battery.min_charging_power:
                # charge
                bat_power = battery.load(self.interval, power)["avg_power"]
            else:
                # positive difference, but below minimum charging power
                bat_power = 0
            gc.add_load(bid, bat_power)


    def step(self, event_list=[]):
        super().step(event_list)

        charging_stations = {}

        # if core standing times are provided, only charge if in core standing time
        if timestep_within_window(self.core_standing_time, current_datetime=self.current_time):
            if self.LOAD_STRAT == "balanced_vehicle":
                assert self.core_standing_time is not None, (
                            "Provide core standing times in the generate_schedule.cfg to use balanced_vehicle.")
                charging_stations = self.charge_cars_balanced()
            else:
                charging_stations = self.charge_cars()

        # always try to charge/discharge stationary batteries
        self.utilize_stationary_batteries()

        return {'current_time': self.current_time, 'commands': charging_stations}
