import datetime

from src import events, util
from src.strategy import Strategy


class V2g(Strategy):
    """
    Charging strategy that can feed energy back.
    """
    def __init__(self, constants, start_time, **kwargs):
        # defaults, can be overridden by CLO (through kwargs)

        # compare close floating points
        self.EPS = 1e-2
        # low pass filter for predicted external cost
        self.LOAD_STRAT = 'needy' # greedy, needy, balanced
        self.SAFE_DISCHARGE = 0
        self.USE_COST = 0

        # init parent class Strategy. May override defaults
        super().__init__(constants, start_time, **kwargs)
        self.description = "v2g ({})".format(self.LOAD_STRAT)

        # limitations: one grid connector, linear costs
        assert len(constants.grid_connectors) == 1, "Only one grid connector supported"
        self.gc = list(self.world_state.grid_connectors.values())[0]

        # set order of vehicles to load
        if self.LOAD_STRAT == 'greedy':
            self.sort_key=lambda v: v.estimated_time_of_departure
        elif self.LOAD_STRAT == 'needy':
            self.sort_key=lambda v: -v.get_delta_soc()*v.battery.capacity
        elif self.LOAD_STRAT == 'balanced':
            self.sort_key=lambda v: 0 # order does not matter
        else:
            raise NotImplementedError(self.LOAD_STRAT)

    def step(self, event_list=[]):
        super().step(event_list)

        # reset charging station power
        # may not be needed
        for cs in self.world_state.charging_stations.values():
            cs.current_power = 0

        energy_needed = 0
        energy_avail  = 0
        max_energy_needed = 0
        # get connected vehicles
        vehicles = {}
        for vid, vehicle in self.world_state.vehicles.items():
            if vehicle.connected_charging_station is not None:
                vehicles[vid] = vehicle
                delta_soc = vehicle.get_delta_soc()
                energy_needed += max(delta_soc / 100, 0) * vehicle.battery.capacity
                energy_avail  -= min(delta_soc / 100, 0) * vehicle.battery.capacity
                max_energy_needed += (1 - vehicle.battery.soc/100) * vehicle.battery.capacity

        # get one and only GC
        gc_id, gc = list(self.world_state.grid_connectors.items())[0]

        # look at next 24h
        # in this time, all vehicles must be charged
        # get future events and predict external load and cost for each timestep
        event_idx = 0
        timesteps_per_day = int(datetime.timedelta(days = 1) / self.interval)
        hours_per_timestep=self.interval.total_seconds() / 3600.0
        timesteps = []
        gc_info = {
            'max_power': gc.cur_max_power,
            'cost': gc.cost,
            'load': gc.get_current_load()
        }
        min_cost = util.get_cost(0, gc.cost)
        max_cost = util.get_cost(gc.cur_max_power, gc.cost)

        cur_time = self.current_time - self.interval
        for step_idx in range(timesteps_per_day):
            cur_time += self.interval

            # still vehicles present at this timestep?
            vehicles_present = False
            for vehicle in vehicles.values():
                still_present  = vehicle.estimated_time_of_departure > cur_time
                if still_present:
                    vehicles_present = True
                    break

            if not vehicles_present:
                # stop when no vehicle are left
                break

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
                    max_power = event.max_power or gc_info['max_power']
                    gc_info['max_power'] = min(gc_info['max_power'], max_power)
                    gc_info['cost'] = event.cost

            # predicted external load
            if step_idx > 0:
                gc_info['load'] = gc.get_avg_ext_load(cur_time, self.interval)
                min_cost = min(min_cost, util.get_cost(0, gc_info['cost']))
                max_cost = max(max_cost, util.get_cost(gc_info['max_power'], gc_info['cost']))

            timesteps.append(gc_info.copy())

        if len(timesteps) == 0:
            # no timesteps -> no charging vehicle
            socs={vid: v.battery.soc for vid, v in self.world_state.vehicles.items()}
            return {'current_time': self.current_time, 'commands': {}, 'socs': socs}

        if self.USE_COST == 0:
            min_power = 0
            max_power = max([info['max_power'] for info in timesteps])

            while max_power - min_power > self.EPS:
                cur_power = (max_power + min_power) / 2
                energy_loaded = 0
                energy_discharged = 0
                for ts_idx, gc_info in enumerate(timesteps):
                    usable_power = min(cur_power, gc_info['max_power'])
                    usable_power -= gc_info['load']
                    if usable_power > 0:
                        # load vehicles
                        energy_loaded += usable_power * hours_per_timestep
                    else:
                        # use vehicles to support external load
                        energy_discharged -= usable_power * hours_per_timestep

                if energy_loaded < energy_needed:
                    # fail: not enough charged
                    min_power = cur_power
                elif energy_loaded + energy_avail > energy_discharged:
                    # loaded too much
                    max_power = cur_power
                else:
                    # success: vehicles charged, try to build buffer
                    min_power = cur_power

            usable_power = min(gc.cur_max_power, cur_power) - gc.get_current_load()
        elif util.get_cost(gc.cur_max_power, gc.cost) < 0:
            # negative price: full power
            usable_power = gc.cur_max_power - gc.get_current_load()
        elif self.USE_COST == 1:
            min_power = 0
            max_power = max([info['max_power'] for info in timesteps])
            last_cost = None
            increase = None
            while max_power - min_power > self.EPS:
                cur_power = (max_power + min_power) / 2
                energy_loaded = 0
                energy_discharged = 0
                cost = 0
                for ts_idx, gc_info in enumerate(timesteps):
                    total_power = min(cur_power, gc_info['max_power'])
                    # use saved energy to reduce external load
                    usable_power = total_power - gc_info['load']
                    cost += util.get_cost(usable_power, gc_info['cost'])
                    if usable_power > 0:
                        # load vehicles
                        energy_loaded += usable_power * hours_per_timestep
                    else:
                        # use vehicles to support external load
                        energy_discharged -= usable_power * hours_per_timestep

                if energy_loaded < energy_needed:
                    # fail: not enough charged
                    min_power = cur_power
                    increase = True
                elif increase is None:
                    # first iteration: check direction
                    if last_cost is None:
                        # check higher power (easier to reset)
                        min_power = cur_power
                        # don't set increase yet
                    else:
                        # second iteration: check success of higher power
                        if cost <= last_cost:
                            # success! Higher power is cheaper
                            # continue raising power
                            min_power = cur_power
                            increase = True
                        else:
                            # oops, higher power is more expensive
                            # reset last iteration
                            min_power = 0
                            # check lower cost
                            max_power = cur_power
                            increase = False
                else:
                    # later iterations: check cost change
                    if cost < last_cost:
                        # cheaper: continue same way
                        if increase:
                            min_power = cur_power
                        else:
                            max_power = cur_power
                    elif cost == last_cost:
                        # no change: try higher power
                        min_power = cur_power
                        increase = True
                    else:
                        # more expensive: go other way
                        if increase:
                            max_power = cur_power
                        else:
                            min_power = cur_power
                        increase = not increase
                last_cost = cost

            usable_power = min(gc.cur_max_power, cur_power) - gc.get_current_load()
        else:
            # get power by price
            while max_cost - min_cost > self.EPS:
                cur_cost = (max_cost + min_cost) / 2
                energy_loaded = 0
                energy_discharged = 0

                for ts_idx, gc_info in enumerate(timesteps):
                    total_power = util.get_power(cur_cost, gc_info['cost'])
                    total_power = min(total_power, gc_info['max_power'])
                    usable_power = total_power - gc_info['load']
                    if usable_power > 0:
                        # load vehicles
                        energy_loaded += usable_power * hours_per_timestep
                    else:
                        # use vehicles to support external load
                        energy_discharged -= usable_power * hours_per_timestep

                if energy_loaded < energy_needed:
                    # fail: not enough charged
                    min_cost = cur_cost
                elif energy_loaded + energy_avail > energy_discharged:
                    # success, but surplus energy
                    max_cost = cur_cost
                else:
                    min_cost = cur_cost
            usable_power = util.get_power(cur_cost, gc.cost)
            usable_power = min(usable_power, gc.cur_max_power)
            usable_power -= gc.get_current_load()

        charging_stations = {}
        vehicle_list = sorted(vehicles.values(), key=self.sort_key)
        if usable_power > 0:
            # load vehicles
            # prioritize vehicles below desired SOC
            vehicles = list(filter(lambda v: v.get_delta_soc() > 0, vehicle_list))
            used_power = 0
            for vehicle in vehicles:
                if self.LOAD_STRAT == 'greedy':
                    # charge one vehicle after the other
                    p = max(usable_power - used_power, 0)
                    load = vehicle.battery.load(self.interval, p)
                elif self.LOAD_STRAT == 'needy' and energy_needed > 0:
                    f = vehicle.get_delta_soc()/100 * vehicle.battery.capacity / energy_needed
                    load = vehicle.battery.load(self.interval, usable_power * f)
                elif self.LOAD_STRAT == 'balanced':
                    # distribute among vehicles in need
                    load = vehicle.battery.load(self.interval, usable_power / len(vehicles))

                cs_id = vehicle.connected_charging_station
                cs = self.world_state.charging_stations[cs_id]
                avg_power = load["avg_power"]
                charging_stations[cs_id] = gc.add_load(cs_id, avg_power)
                cs.current_power += avg_power
                used_power += avg_power
                max_energy_needed -= load["soc_delta"]/100 * vehicle.battery.capacity

            # distribute surplus
            surplus_power = max(usable_power - used_power, 0)
            for vehicle in vehicle_list:
                cs_id = vehicle.connected_charging_station
                cs = self.world_state.charging_stations[cs_id]
                cs_remaining_power = cs.max_power - cs.current_power
                load_power = 0
                # if usable_power <= 0: # FIXME: does not correctly check for min_charging_power
                if usable_power < vehicle.vehicle_type.min_charging_power:
                    break
                if self.LOAD_STRAT == 'greedy':
                    # charge one vehicle after the other
                    load_power = max(usable_power - used_power, 0)
                elif self.LOAD_STRAT == 'needy' and max_energy_needed > 0:
                    delta_soc = 1 - vehicle.battery.soc/100
                    f = delta_soc * vehicle.battery.capacity / max_energy_needed
                    load_power = surplus_power * f
                elif self.LOAD_STRAT == 'balanced':
                    # distribute among vehicles
                    load_power = surplus_power / len(vehicle_list)

                load_power = min(cs_remaining_power, load_power)
                avg_power = vehicle.battery.load(self.interval, load_power)["avg_power"]
                used_power += avg_power

                charging_stations[cs_id] = gc.add_load(cs_id, avg_power)
                cs.current_power += avg_power

        else:
            # discharge vehicles
            vehicle_list.reverse()
            usable_power *= -1
            for vehicle in vehicle_list:
                if usable_power <= 0:
                    # all available power used
                    break
                if vehicle.get_delta_soc() >= self.SAFE_DISCHARGE:
                    # vehicle is not charged yet
                    continue

                if self.LOAD_STRAT == 'greedy':
                    # discharge one vehicle after the other
                    avg_power = vehicle.battery.unload(self.interval, usable_power, vehicle.desired_soc)['avg_power']
                    usable_power -= avg_power
                elif self.LOAD_STRAT == 'needy' and energy_avail > 0:
                    # discharge in relation to available energy
                    delta_soc = -vehicle.get_delta_soc() / 100
                    f = delta_soc * vehicle.battery.capacity / energy_avail
                    avg_power = vehicle.battery.unload(self.interval, usable_power * f, vehicle.desired_soc)['avg_power']
                elif self.LOAD_STRAT == 'balanced':
                    # discharge evenly
                    avg_power = vehicle.battery.unload(self.interval, usable_power / len(vehicle_list), vehicle.desired_soc)['avg_power']

                cs_id = vehicle.connected_charging_station
                cs = self.world_state.charging_stations[cs_id]
                charging_stations[cs_id] = gc.add_load(cs_id, -avg_power)
                cs.current_power -= avg_power

        socs={vid: v.battery.soc for vid, v in self.world_state.vehicles.items()}
        return {'current_time': self.current_time, 'commands': charging_stations, 'socs': socs}
