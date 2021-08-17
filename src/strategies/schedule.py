from src.strategy import Strategy
from src.util import clamp_power


class Schedule(Strategy):
    def __init__(self, constants, start_time, **kwargs):
        self.LOAD_STRAT = 'needy'  # greedy, balanced
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
        else:
            "Unknown charging startegy: {}".format(self.LOAD_STRAT)

    def step(self, event_list=[]):
        super().step(event_list)

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
                soc_needed = 1 - vehicle.battery.soc
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
                avg_power = vehicle.battery.load(self.interval, power)["avg_power"]
                cs_id = vehicle.connected_charging_station
                charging_stations[cs_id] = gc.add_load(cs_id, avg_power)

        # adjust deviation with batteries
        for bid, battery in self.world_state.batteries.items():
            gc_id = battery.parent
            gc = self.world_state.grid_connectors[gc_id]
            if gc.target is None:
                # no schedule set
                continue
            # get difference between target and GC load
            power = gc.target - gc.get_current_load()
            if power < 0:
                # discharge
                bat_power = -battery.unload(self.interval, -power)["avg_power"]
            elif power > battery.min_charging_power:
                # charge
                bat_power = battery.load(self.interval, power)["avg_power"]
            else:
                # positive difference, but below minimum charging power
                bat_power = 0
            gc.add_load(bid, bat_power)
        return {'current_time': self.current_time, 'commands': charging_stations}
