from copy import deepcopy
from datetime import timedelta

import src.events as events
from src.strategy import Strategy
from src.util import clamp_power, timestep_within_window, dt_to_end_of_time_window


class Schedule(Strategy):
    def __init__(self, constants, start_time, **kwargs):
        self.LOAD_STRAT = 'needy'  # greedy, balanced
        self.currently_in_core_standing_time = False
        self.ITERATIONS = 12

        super().__init__(constants, start_time, **kwargs)

        self.description = "schedule ({})".format(self.LOAD_STRAT)
        if self.LOAD_STRAT == "greedy":
            self.sort_key = lambda v: (
                v[0].battery.soc >= v[0].desired_soc,
                v[0].estimated_time_of_departure)
        elif self.LOAD_STRAT == "needy" or self.LOAD_STRAT == "balanced_vehicle":
            # charge cars with not much power needed first, may leave more for others
            self.sort_key = lambda v: v[0].get_delta_soc() * v[0].battery.capacity
        elif self.LOAD_STRAT == "balanced":
            # only relevant if not enough power to charge all vehicles
            self.sort_key = lambda v: v[0].estimated_time_of_departure
        else:
            "Unknown charging startegy: {}".format(self.LOAD_STRAT)

    def sim_charging_process(self, vehicle, dt, max_power, delta_soc=None):
        # get vehicle
        cs_id = vehicle.connected_charging_station
        if cs_id is None:
            # not connected
            return None
        # get connected charging station
        cs = self.world_state.charging_stations[cs_id]
        power = 0
        charged_soc = 0
        delta_soc = delta_soc if delta_soc is not None else vehicle.get_delta_soc()

        if delta_soc > self.EPS:
            # get limits
            min_power = max(vehicle.vehicle_type.min_charging_power, cs.min_power)
            max_power = min(max_power, vehicle.vehicle_type.charging_curve.max_power)
            max_power = clamp_power(max_power, vehicle, cs)
            # time until departure
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

        return {"opt_power": power, "charged_soc": charged_soc}

    def collect_future_gc_info(self, dt=timedelta(days=1)):
        # TODO change signal time extLoad in init

        gc = list(self.world_state.grid_connectors.values())[0]

        # GC info for each future timestep until all cars left
        gc_info = [{
            # "charging": set(),
            "current_loads": {k: v for k, v in gc.current_loads.items()},
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
                    gc_info[-1]["target"] = \
                        event.target if event.target is not None else gc_info[-1]["target"]
                elif type(event) == events.ExternalLoad:
                    gc_info[-1]["current_loads"][event.name] = event.value
                elif type(event) == events.EnergyFeedIn:
                    gc_info[-1]["current_loads"][event.name] = -event.value
                # ignore vehicle events, use vehicle data directly
                # ignore feedIn for now as well
            # end of useful events peek into future events for external loads, schedule
        return gc_info

    def evaluate_core_standing_time_ahead(self):
        TS_per_hour = (timedelta(hours=1) / self.interval)
        dt_to_end_core_standing_time = dt_to_end_of_time_window(self.current_time,
                                                                self.core_standing_time,
                                                                self.interval)

        gc_infos = self.collect_future_gc_info(dt_to_end_core_standing_time)
        self.power_for_cars_per_TS = [
            gc_info.get("target") - sum(gc_info["current_loads"].values())
            for gc_info in gc_infos
        ]
        TS_to_charge_cars = sum([1 for power in self.power_for_cars_per_TS if power > self.EPS])

        self.energy_available_for_cars_on_schedule = sum([
            power / TS_per_hour
            for power in self.power_for_cars_per_TS
        ])

        self.energy_needed_per_vehicle = {}
        for vehicle_id, vehicle in self.world_state.vehicles.items():
            delta_soc = vehicle.get_delta_soc()
            self.energy_needed_per_vehicle[vehicle_id] = (delta_soc
                                                          * vehicle.battery.capacity
                                                          / vehicle.battery.efficiency
                                                          if delta_soc > self.EPS else 0)

        self.extra_energy_per_vehicle = {}
        # TODO calc average power per TS and choose max power for load process as
        # MIN(avg_power, max_charging_power_vehicle)
        # average_power_to_charge_cars = 11
        for vehicle_id, vehicle in self.world_state.vehicles.items():
            old_soc = vehicle.battery.soc
            vehicle.battery.load(TS_to_charge_cars * self.interval,
                                 vehicle.vehicle_type.charging_curve.max_power,
                                 target_soc=vehicle.desired_soc)
            delta_soc = vehicle.get_delta_soc()
            self.extra_energy_per_vehicle[vehicle_id] = delta_soc if delta_soc > self.EPS else 0

            vehicle.battery.soc = old_soc

        self.currently_in_core_standing_time = True

    def charge_cars_balanced(self):

        charging_stations = {}
        TS_per_hour = (timedelta(hours=1) / self.interval)
        # TODO get back from util
        dt_to_end_core_standing_time = dt_to_end_of_time_window(self.current_time,
                                                                self.core_standing_time,
                                                                self.interval)

        TS_to_charge_cars = sum([1 for power in self.power_for_cars_per_TS if power > self.EPS])
        power_to_charge_cars = self.power_for_cars_per_TS.pop(0)

        if power_to_charge_cars < self.EPS:  # charge over schedule
            dt = dt_to_end_core_standing_time - TS_to_charge_cars * self.interval
            for vehicle_id, delta_soc in self.extra_energy_per_vehicle.items():
                vehicle = self.world_state.vehicles[vehicle_id]
                cs_id = vehicle.connected_charging_station
                assert cs_id is not None, (
                    f"Vehicle {vehicle_id} not available during core standing time!"
                )
                # get connected charging station
                cs = self.world_state.charging_stations[cs_id]
                gc = self.world_state.grid_connectors[cs.parent]
                vehicle = self.world_state.vehicles[vehicle_id]
                # find optimal power for charging
                power = self.sim_charging_process(vehicle,
                                                  dt,
                                                  vehicle.vehicle_type.charging_curve.max_power,
                                                  delta_soc=delta_soc
                                                  )["opt_power"]
                # load with power
                avg_power, charged_soc = vehicle.battery.load(self.interval,
                                                              power,
                                                              target_soc=vehicle.desired_soc
                                                              ).values()
                self.extra_energy_per_vehicle[vehicle_id] -= charged_soc
                charging_stations[cs_id] = gc.add_load(cs_id, avg_power)
        else:  # charge on schedule
            try:
                # fraction of energy distributed in this timestep
                fraction = (power_to_charge_cars / TS_per_hour
                            / self.energy_available_for_cars_on_schedule)
            except ZeroDivisionError:
                # energy available for charing cars might be zero
                fraction = 0

            extra_power = 0
            vehicles = sorted(self.energy_needed_per_vehicle.items(), key=lambda i: i[1])
            while len(vehicles) > 0:
                vehicle_id, energy_needed = vehicles.pop(0)
                vehicle = self.world_state.vehicles[vehicle_id]
                cs_id = vehicle.connected_charging_station
                assert cs_id is not None, (
                    f"Vehicle {vehicle_id} not available during core standing time!"
                )
                # get connected charging station and grid connector
                cs = self.world_state.charging_stations[cs_id]
                gc = self.world_state.grid_connectors[cs.parent]

                # boundaries of charging process
                remaining_power_on_schedule = gc.target - gc.get_current_load()
                if remaining_power_on_schedule < self.EPS:
                    break
                power_allocated_for_vehicle = fraction * energy_needed * TS_per_hour + extra_power

                power = min(remaining_power_on_schedule, power_allocated_for_vehicle)
                power = clamp_power(power, vehicle, cs)

                # load with power
                avg_power, charged_soc = vehicle.battery.load(self.interval,
                                                              power,
                                                              target_soc=vehicle.desired_soc
                                                              ).values()
                charging_stations[cs_id] = gc.add_load(cs_id, avg_power)
                extra_power = power_allocated_for_vehicle - avg_power
                if (power_allocated_for_vehicle == extra_power and
                        remaining_power_on_schedule >= cs.min_power and
                        remaining_power_on_schedule >= vehicle.vehicle_type.min_charging_power and
                        vehicle.get_delta_soc() > self.EPS):
                    # vehicle didnt get to charge, going to the back of the line
                    vehicles.append((vehicle_id, energy_needed))
                # can active charging station bear minimum load?
                assert cs.max_power >= cs.current_power - self.EPS, (
                    "{} - {} over maximum load ({} > {})".format(
                        self.current_time, cs_id, cs.current_power, cs.max_power))
                # can grid connector bear load?
                assert gc.cur_max_power >= gc.get_current_load() - self.EPS, (
                    "{} - {} over maximum load ({} > {})".format(
                        self.current_time, cs.parent, gc.get_current_load(), gc.cur_max_power))

        # last timestep of core standing time - reset everything
        if dt_to_end_core_standing_time <= self.interval:
            self.currently_in_core_standing_time = False

        return charging_stations

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
                elif self.LOAD_STRAT == "balanced_vehicle":
                    power = max(-gc.get_current_load(), 0)

                power = clamp_power(power, vehicle, cs)
                avg_power = vehicle.battery.load(self.interval,
                                                 power,
                                                 target_soc=vehicle.desired_soc)["avg_power"]
                cs_id = vehicle.connected_charging_station
                charging_stations[cs_id] = gc.add_load(cs_id, avg_power)

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

        if self.LOAD_STRAT == "balanced_vehicle":
            assert self.core_standing_time is not None, (
                "Provide core standing times in the generate_schedule.cfg"
                "to use balanced_vehicle.")

            if timestep_within_window(self.core_standing_time, self.current_time):
                # only run in first TS of core standing time
                if not self.currently_in_core_standing_time:
                    self.evaluate_core_standing_time_ahead()
                charging_stations = self.charge_cars_balanced()
            else:
                charging_stations = self.charge_cars()
        else:
            # substrats "needy", "greedy", "balanced"
            charging_stations = self.charge_cars()

        # always try to charge/discharge stationary batteries
        self.utilize_stationary_batteries()

        return {'current_time': self.current_time, 'commands': charging_stations}
