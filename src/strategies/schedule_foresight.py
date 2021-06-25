from copy import deepcopy
from datetime import timedelta

import src.events as events
from src.strategy import Strategy
from src.util import clamp_power


def get_power_needed(vehicle):
    return (1 - vehicle.battery.soc/100) * vehicle.battery.capacity


class ScheduleForesight(Strategy):
    def __init__(self, constants, start_time, **kwargs):
        self.ITERATIONS = 12
        self.LOAD_STRAT = 'needy'  # greedy, balanced
        self.MAX_DEV = 10  # maximum permitted deviation from schedule in kWh
        super().__init__(constants, start_time, **kwargs)

        self.description = "schedule foresight ({})".format(self.LOAD_STRAT)
        if self.LOAD_STRAT == "greedy":
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
            "Unknown charging startegy: {}".format(self.LOAD_STRAT)

        assert len(self.world_state.grid_connectors) == 1, "Only one grid connector supported"

    def step(self, event_list=[]):
        super().step(event_list)

        gc = list(self.world_state.grid_connectors.values())[0]

        vehicles = {}

        gc_info = [{
            "charging": set(),
            "target": gc.target
        }]

        power_needed = 0

        for vid, vehicle in self.world_state.vehicles.items():
            cs_id = vehicle.connected_charging_station
            if cs_id is None:
                continue
            vehicles[vid] = vehicle
            gc_info[0]["charging"].add(vid)
            power_needed += get_power_needed(vehicle)

        # peek into future events for vehicle events and schedule
        # external loads and feed-in ignored
        event_idx = 0
        cur_time = self.current_time - self.interval
        timesteps_per_day = timedelta(days=1) // self.interval
        for timestep_idx in range(timesteps_per_day):
            cur_time += self.interval

            if timestep_idx > 0:
                # copy last GC info
                gc_info.append(gc_info[-1])

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
                event_idx += 1
                if type(event) == events.GridOperatorSignal:
                    # update GC info
                    gc_info[-1]["target"] = event.target or 0
                elif type(event) == events.VehicleEvent:
                    vid = event.vehicle_id
                    if event.event_type == "departure":
                        gc_info[-1]["charging"].remove(vid)
            # end of useful events

            if len(gc_info[-1]["charging"]) == 0:
                # all vehicles left
                break

        # adjust target in each timestep
        allowed_min_dev = -self.MAX_DEV
        allowed_max_dev = self.MAX_DEV

        idx = 0
        safe = False

        sim_vehicles = deepcopy(vehicles)

        while (idx < self.ITERATIONS or not safe) and allowed_max_dev - allowed_min_dev > self.EPS:

            # reset simulated SoC
            for vid, sim_vehicle in sim_vehicles.items():
                sim_vehicle.battery.soc = vehicles[vid].battery.soc

            # get new deviation from target
            cur_dev = (allowed_min_dev + allowed_max_dev) / 2
            sum_dev = 0

            for ts_idx, ts_info in enumerate(gc_info):
                target = ts_info["target"]
                sim_power_needed = 0
                sim_charging = []

                safe = True
                for vid, sim_vehicle in sim_vehicles.items():
                    if vid not in ts_info["charging"]:
                        # vehicle left
                        safe &= sim_vehicle.get_delta_soc() < self.EPS
                    else:
                        sim_power_needed += get_power_needed(sim_vehicle)
                        sim_charging.append(sim_vehicle)

                if not safe:
                    # not sufficiently charged: increase power level
                    allowed_min_dev = cur_dev
                    break

                sim_charge = self.charge_vehicles(sim_charging, target + cur_dev, sim_power_needed)
                sum_dev += target - sum(sim_charge.values())
            else:
                # not failed during simulation

                if safe and abs(cur_dev) < self.EPS:
                    # can be charged with given schedule
                    break

                # get remaining flexibility
                flexibility = sum([get_power_needed(v) for v in sim_vehicles.values()])
                if sum_dev > flexibility:
                    # charged too much
                    allowed_max_dev = cur_dev
                else:
                    # can be charged more
                    allowed_min_dev = cur_dev

        # charge for real
        commands = self.charge_vehicles(vehicles.values(), gc.target + cur_dev, power_needed)
        for cs_id, avg_power in commands.items():
            gc.add_load(cs_id, avg_power)
        return {'current_time': self.current_time, 'commands': commands}

    def charge_vehicles(self, vehicles_list, total_power, power_needed):
        vehicles_list = sorted(vehicles_list, key=self.sort_key)
        available_power = total_power
        vehicles_to_charge = len(vehicles_list)
        charging_stations = {}

        for vehicle in vehicles_list:
            if self.LOAD_STRAT == "greedy":
                # use maximum of given power
                power = available_power
            elif self.LOAD_STRAT == "needy":
                # get fraction of precalculated power need to overall power need
                vehicle_power_needed = get_power_needed(vehicle)
                if power_needed > self.EPS:
                    power = available_power * vehicle_power_needed / power_needed
                else:
                    power = 0
                power_needed -= vehicle_power_needed
            elif self.LOAD_STRAT == "balanced":
                power = available_power / vehicles_to_charge

            cs_id = vehicle.connected_charging_station
            cs = self.world_state.charging_stations[cs_id]
            power = clamp_power(power, vehicle, cs)
            avg_power = vehicle.battery.load(self.interval, power)["avg_power"]
            charging_stations[cs_id] = avg_power
            available_power -= avg_power
            vehicles_to_charge -= 1

        return charging_stations
