import datetime

import events
from strategy import Strategy
import util


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

        self.pred_ext_load = get_avg_ext_load()

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
            'load': gc.get_external_load()
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
                midnight = cur_time.replace(hour=0, minute=0)
                weekday_idx  = cur_time.weekday()
                interval_idx = int((cur_time - midnight) / self.interval)
                gc_info['load'] = self.pred_ext_load[weekday_idx][interval_idx]
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

            usable_power = min(gc.cur_max_power, cur_power) - gc.get_external_load()
        elif util.get_cost(gc.cur_max_power, gc.cost) < 0:
            # negative price: full power
            usable_power = gc.cur_max_power - gc.get_external_load()
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

            usable_power = min(gc.cur_max_power, cur_power) - gc.get_external_load()
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
            usable_power -= gc.get_external_load()

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
                # if usable_power <= 0:
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

def get_avg_ext_load():
    return [
        [13.88, 13.88, 14.0, 13.87, 13.9, 13.94, 13.81, 13.88, 13.85, 13.85, 13.81, 13.87, 14.0, 13.83, 14.17, 14.06, 14.9, 16.48, 17.79, 19.63, 31.46, 43.08, 45.17, 48.21, 60.62, 67.23, 66.15, 61.29, 60.52, 61.63, 62.73, 58.35, 53.44, 50.1, 46.5, 40.35, 35.6, 31.46, 28.6, 26.29, 24.73, 22.13, 21.17, 20.85, 20.1, 19.87, 19.46, 19.17, 19.52, 20.37, 21.73, 21.85, 21.58, 21.87, 22.23, 21.69, 21.21, 19.98, 19.85, 20.15, 20.02, 20.23, 20.12, 20.33, 20.98, 21.48, 21.21, 21.75, 22.71, 24.29, 29.12, 32.9, 30.96, 29.79, 30.17, 27.83, 24.65, 22.33, 21.38, 19.08, 17.79, 15.96, 15.63, 15.77, 15.48, 15.0, 14.69, 14.48, 14.21, 14.15, 14.04, 13.94, 13.96, 13.96, 14.04, 14.1],
        [14.08, 14.08, 14.13, 14.09, 13.94, 14.11, 14.06, 14.02, 13.96, 14.13, 13.92, 14.08, 13.91, 14.09, 14.26, 14.36, 15.83, 23.66, 36.3, 39.23, 48.57, 61.13, 67.57, 68.91, 74.55, 75.02, 71.94, 71.81, 73.62, 72.51, 70.62, 71.83, 71.62, 65.87, 57.85, 51.79, 48.6, 42.3, 36.6, 30.96, 25.62, 23.75, 23.51, 22.06, 21.25, 20.08, 20.55, 19.81, 20.89, 20.98, 21.3, 20.77, 21.13, 21.77, 20.89, 20.58, 20.77, 20.4, 19.55, 19.94, 20.38, 20.49, 20.58, 20.38, 20.51, 20.92, 21.55, 23.45, 23.83, 25.75, 28.74, 31.72, 32.25, 30.77, 29.85, 29.0, 27.13, 25.09, 22.87, 20.68, 18.47, 16.21, 15.34, 15.23, 14.83, 14.64, 14.26, 14.32, 14.04, 14.02, 13.91, 14.02, 13.89, 13.92, 13.85, 14.02],
        [13.94, 13.96, 13.88, 14.02, 13.88, 13.98, 14.06, 14.0, 13.98, 14.27, 14.04, 14.08, 14.12, 14.29, 14.48, 14.42, 15.98, 22.29, 35.71, 40.27, 51.42, 62.33, 70.08, 74.12, 78.63, 77.58, 77.08, 77.08, 76.63, 74.23, 72.88, 73.87, 69.98, 62.79, 53.69, 50.92, 45.5, 39.9, 33.33, 28.96, 26.0, 24.63, 23.42, 22.5, 21.46, 21.06, 20.15, 20.15, 20.42, 20.29, 20.23, 21.37, 21.46, 20.37, 19.79, 20.04, 19.6, 19.79, 21.13, 21.17, 22.4, 21.38, 20.71, 21.1, 22.23, 22.6, 22.25, 23.1, 25.04, 28.33, 32.65, 34.04, 32.04, 31.56, 30.79, 29.13, 28.4, 24.98, 21.44, 19.62, 17.65, 16.9, 15.88, 15.87, 15.44, 14.87, 14.6, 14.37, 14.13, 14.1, 14.17, 14.0, 14.12, 14.0, 14.08, 14.08],
        [13.98, 14.08, 14.13, 14.1, 14.0, 14.08, 14.0, 13.9, 14.23, 14.15, 14.19, 14.06, 14.13, 14.38, 14.38, 14.4, 16.15, 22.23, 36.27, 39.5, 50.29, 62.83, 69.33, 72.96, 76.0, 75.77, 74.33, 74.56, 75.15, 73.75, 73.02, 72.81, 68.54, 60.9, 52.96, 45.88, 40.71, 38.1, 32.62, 29.35, 26.12, 23.67, 22.6, 22.13, 20.96, 20.67, 20.13, 20.13, 19.65, 19.87, 20.17, 20.08, 19.83, 20.29, 21.35, 20.52, 20.81, 20.67, 20.21, 20.19, 20.27, 21.02, 21.33, 20.69, 20.5, 21.02, 22.71, 23.15, 24.12, 25.71, 28.96, 32.25, 34.67, 33.75, 31.73, 30.77, 28.87, 26.02, 22.19, 19.81, 18.06, 17.52, 16.96, 16.35, 16.02, 15.44, 14.46, 13.98, 13.92, 13.85, 13.85, 13.83, 13.98, 13.85, 13.92, 13.9],
        [13.83, 13.81, 13.79, 13.83, 13.71, 13.71, 13.85, 13.79, 13.85, 13.81, 13.77, 13.9, 13.83, 13.69, 14.0, 14.29, 16.12, 22.06, 35.48, 37.87, 48.25, 59.23, 67.5, 70.27, 75.46, 74.4, 75.37, 76.38, 74.83, 73.08, 75.79, 74.13, 68.63, 61.48, 54.38, 49.4, 44.58, 39.52, 35.81, 30.75, 27.15, 25.58, 24.79, 22.92, 22.04, 21.5, 21.94, 21.52, 21.75, 22.56, 22.79, 22.62, 22.58, 22.46, 21.48, 20.92, 20.58, 21.04, 21.52, 21.5, 21.29, 22.12, 22.5, 22.04, 22.54, 23.52, 23.17, 23.37, 24.96, 26.75, 30.85, 34.5, 33.85, 32.9, 31.08, 30.63, 28.69, 26.58, 25.31, 22.44, 20.44, 17.73, 16.73, 17.06, 15.35, 15.25, 15.02, 14.35, 14.52, 13.94, 14.02, 14.0, 14.12, 14.02, 14.0, 13.92],
        [14.06, 13.87, 13.94, 13.98, 13.87, 13.87, 13.94, 13.9, 13.94, 13.98, 13.88, 13.94, 13.92, 13.94, 14.25, 14.17, 15.94, 20.52, 31.19, 35.77, 44.13, 58.98, 66.06, 64.94, 67.77, 71.71, 72.4, 73.94, 73.44, 73.13, 72.75, 70.1, 66.73, 59.65, 52.88, 47.96, 42.19, 37.13, 34.83, 32.52, 31.9, 30.44, 28.0, 25.04, 23.29, 24.12, 24.25, 25.37, 26.23, 25.12, 22.87, 20.65, 19.08, 18.17, 17.48, 17.81, 18.77, 18.71, 19.58, 19.37, 19.67, 20.02, 19.94, 20.73, 21.0, 21.69, 23.75, 24.58, 25.21, 25.88, 25.65, 24.83, 24.31, 24.63, 24.12, 22.02, 19.81, 17.75, 16.29, 15.92, 15.48, 15.54, 15.37, 16.06, 15.88, 14.96, 14.42, 13.96, 13.98, 14.0, 14.04, 13.9, 13.92, 13.94, 13.96, 13.83],
        [13.85, 14.04, 13.98, 13.83, 13.94, 13.9, 13.94, 13.92, 13.98, 13.83, 13.87, 13.77, 13.96, 13.85, 13.62, 13.42, 13.38, 13.27, 13.15, 13.12, 12.83, 12.75, 12.52, 12.58, 12.29, 12.33, 12.1, 12.13, 11.98, 11.63, 11.79, 11.65, 11.4, 11.13, 11.15, 11.21, 11.31, 11.17, 11.19, 11.23, 11.23, 11.19, 11.21, 11.31, 11.23, 11.19, 11.23, 11.27, 11.13, 11.44, 11.13, 11.29, 11.37, 11.31, 11.13, 11.33, 11.33, 11.33, 11.29, 11.44, 11.33, 11.23, 11.33, 11.38, 11.46, 11.67, 11.83, 12.0, 12.12, 12.15, 12.38, 12.29, 12.6, 12.54, 12.87, 12.67, 13.04, 12.9, 13.21, 13.25, 13.38, 13.38, 13.96, 13.73, 14.04, 13.81, 13.92, 13.87, 14.0, 13.85, 13.92, 13.87, 13.81, 13.9, 13.94, 13.75]
    ]
