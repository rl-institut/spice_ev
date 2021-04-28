from copy import deepcopy
import datetime

from netz_elog import events, util
from netz_elog.strategy import Strategy


class Inverse(Strategy):
    """
    Charging strategy that prioritizes times with lower power costs.
    Idea is to find minimum viable cost threshold over next 24h
    Timesteps with less external load and smaller costs are prioritized for loading
    """
    def __init__(self, constants, start_time, **kwargs):
        # defaults, can be overridden by CLO (through kwargs)

        # minimum binary seach depth
        self.ITERATIONS = 16
        # compare close floating points
        self.EPS = 1e-5
        self.LOAD_STRAT = 'needy' # greedy, needy, balanced

        # init parent class Strategy. May override defaults
        super().__init__(constants, start_time, **kwargs)
        self.description = "inverse ({})".format(self.LOAD_STRAT)
        # fraction of SOC allowed less
        # eg. margin = 0.05: vehicles are allowed to leave with 95% of desired SOC in price simulation
        # default: margin of strategy
        self.SOC_MARGIN = kwargs.get("SOC_MARGIN", self.margin)

        if self.SOC_MARGIN != self.margin:
            print("WARNING: SoC margins don't match. In price simulation: {}, global: {}".format(self.SOC_MARGIN, self.margin))

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

        # gather info about grid connectors
        timestamp = str(self.current_time.time())
        gcs = {}
        for gc_id, gc in self.world_state.grid_connectors.items():

            gcs[gc_id] = {
                'vehicles': {}, # vehicles to be charged connected to this GC
                'ts': [],       # timestep infos
                'max_power': gc.cur_max_power,
                'feed_in': 0,
                'costs': {
                    'min': util.get_cost(0, gc.cost),
                    'max': util.get_cost(gc.cur_max_power, gc.cost),
                    'cur': gc.cost
                }
            }

        # get connected vehicles
        for vid, vehicle in self.world_state.vehicles.items():
            cs_id = vehicle.connected_charging_station
            if cs_id is not None:
                cs = self.world_state.charging_stations[cs_id]
                gcs[cs.parent]['vehicles'][vid] = vehicle

        # look at next 24h
        # in this time, all vehicles must be charged
        # get future events and predict external load and cost for each timestep
        event_idx = 0
        timesteps_per_day = int(datetime.timedelta(days =1) / self.interval)

        cur_time = self.current_time - self.interval
        for timestep_idx in range(timesteps_per_day):
            cur_time += self.interval

            # still vehicles present at this timestep?
            vehicles_present = False
            for vehicle in self.world_state.vehicles.values():
                still_present = (
                    vehicle.connected_charging_station is not None
                    and vehicle.estimated_time_of_departure is not None
                    and vehicle.estimated_time_of_departure > cur_time)
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
                    gc_id = event.grid_connector_id
                    max_power = event.max_power or gcs[gc_id]['max_power']
                    gcs[gc_id]['max_power'] = min(gcs[gc_id]['max_power'], max_power)
                    gcs[gc_id]['costs']['cur'] = event.cost
                elif type(event) == events.EnergyFeedIn:
                    # update GC info
                    gc_id = event.grid_connector_id
                    gcs[gc_id]['feed_in'] = event.value


            # compute available power and associated costs
            for gc_id, gc in self.world_state.grid_connectors.items():
                # get (predicted) external load
                if timestep_idx == 0:
                    # use actual external load
                    ext_load = gc.get_current_load()
                else:
                    ext_load = gc.get_avg_ext_load(cur_time, self.interval)
                    ext_load -= gcs[gc_id]['feed_in']
                # get cost for no power
                min_power_cost = util.get_cost(0, gcs[gc_id]['costs']['cur'])
                # get cost for max power
                max_power_cost = util.get_cost(gcs[gc_id]['max_power'], gcs[gc_id]['costs']['cur'])

                # new timestep info
                gcs[gc_id]['ts'].append({
                    'max': gcs[gc_id]['max_power'],
                    'ext': ext_load,
                    'costs': gcs[gc_id]['costs']['cur']
                })
                # save min/max costs in GC info
                old_min = gcs[gc_id]['costs']['min']
                old_max = gcs[gc_id]['costs']['max']
                gcs[gc_id]['costs']['min'] = min(old_min, min_power_cost)
                gcs[gc_id]['costs']['max'] = max(old_max, max_power_cost)

        if sum(len(gc['ts']) for gc in gcs.values()) == 0:
            # no timesteps -> no charging at any grid connector
            socs={vid: v.battery.soc for vid, v in self.world_state.vehicles.items()}
            return {'current_time': self.current_time, 'commands': {}, 'socs': socs}

        charging_stations = {}
        # find minimum viable power per grid connector for next 24h
        for gc_id, gc_info in gcs.items():
            gc = self.world_state.grid_connectors[gc_id]

            # retrieve min/max costs for this GC
            min_costs = gc_info['costs']['min']
            max_costs = gc_info['costs']['max']

            # compute cost for upper limit
            cur_max_costs = util.get_cost(gc.cur_max_power, gc.cost)

            # is safe if cost is negative
            safe = cur_max_costs <= 0

            if safe:
                # negative energy price
                # charge all connected vehicles, even above desired_soc
                cur_costs = cur_max_costs
                # skip simulation
                min_costs = cur_max_costs
                max_costs = cur_max_costs
            else:
                cur_costs = None
                # remove all vehicles from simulation where desired SOC is reached
                vehicles = {
                    vid: v for vid,v in gc_info["vehicles"].items()
                    if v.battery.soc < v.desired_soc
                }
                gc_info["vehicles"] = vehicles
                # copy vehicle fleet for simulation (don't change SOC of originals)
                sim_vehicles = deepcopy(vehicles)

            idx = 0
            # try to reach optimum cost level
            # ... at least for ITERATIONS loops
            # ... all vehicles must be loaded (safe result)
            # ... optimum may converge -> min and max must be different
            # ... price may be negative -> min >= max -> skip simulation
            while (idx < self.ITERATIONS or not safe) and max_costs - min_costs > self.EPS:
                idx += 1
                # binary search: try out average of min and max
                cur_costs = (max_costs + min_costs) / 2
                sim_time  = self.current_time - self.interval

                # reset vehicle SOC
                for vid, vehicle in sim_vehicles.items():
                    vehicle.battery.soc = gc_info["vehicles"][vid].battery.soc

                # simulate next 24h
                for ts_info in gc_info['ts']:
                    sim_time += self.interval
                    sim_charging = []

                    # check that any vehicles in need of charging still have time
                    safe = True
                    for vehicle in sim_vehicles.values():
                        needs_charging = vehicle.battery.soc < ((1.0 - self.SOC_MARGIN) * vehicle.desired_soc)
                        has_left = sim_time >= vehicle.estimated_time_of_departure
                        if needs_charging:
                            if has_left:
                                # fail: would have to leave with insufficient charge
                                safe = False
                                break
                            else:
                                # needs charging (preserve order)
                                sim_charging.append(vehicle)

                    if not safe:
                        # at least one vehicle not charged in time:
                        # increase allowed costs
                        min_costs = cur_costs
                        break

                    if len(sim_charging) == 0:
                        # all vehicles left, still valid result:
                        # decrease allowed costs
                        max_costs = cur_costs
                        break


                    # still vehicles left to charge

                    # how much energy can be loaded with current cost?
                    # cur_costs SHOULD be achievable (ValueError otherwise)
                    max_power = util.get_power(cur_costs, ts_info['costs'])

                    # max_power may be None (constant price)
                    # can charge with max_power then
                    max_power = max_power or ts_info['max']
                    # subtract external loads (may be negative because of feed-in)
                    usable_power = max_power - ts_info['ext']
                    self.load_vehicles(sim_charging, usable_power)

                # after all timesteps done: check all vehicles are loaded
                safe = True
                for vehicle in sim_vehicles.values():
                    safe &= vehicle.battery.soc >= ((1.0 - self.SOC_MARGIN) * vehicle.desired_soc)
                # adjust costs based on result
                if safe:
                    max_costs = cur_costs
                else:
                    min_costs = cur_costs

            # get optimum power
            max_power = util.get_power(cur_costs, gc.cost)
            # max_power may be None (constant price)
            max_power = max_power or gc.cur_max_power
            # make sure power is within GC limits (should not be needed?)
            max_power = min(max_power, gc.cur_max_power)
            # subtract external loads
            usable_power = max_power - gc.get_current_load()

            # charge for real, get CS power values
            charging_info = self.load_vehicles(gc_info['vehicles'].values(), usable_power)
            # apply CS power
            for cs_id, power in charging_info.items():
                charging_stations[cs_id] = gc.add_load(cs_id, power)
                cs.current_power += power

        socs={vid: v.battery.soc for vid, v in self.world_state.vehicles.items()}
        return {'current_time': self.current_time, 'commands': charging_stations, 'socs': socs}

    def load_vehicles(self, vehicles, power):
        # load vehicles with specific strategy
        # order by key depending on strategy
        list(vehicles).sort(key=self.sort_key)

        # CS power (return value)
        charging_stations = {}
        # energy needed so that all vehicles reach desired SOC
        desired_energy_need = 0
        # energy needed so that all vehicles reach 100% SOC
        max_energy_need = 0

        # find vehicles in need of charge
        needy_vehicles = []
        for vehicle in vehicles:
            soc = vehicle.battery.soc
            if soc < vehicle.desired_soc:
                # below desired SOC
                needy_vehicles.append(vehicle)
                desired_energy_need += soc / 100 * vehicle.battery.capacity
            max_energy_need += (1 - soc/100) * vehicle.battery.capacity

        # load vehicles below desired SOC first
        used_power = 0
        energy_loaded = 0
        for idx, vehicle in enumerate(needy_vehicles):
            if power - used_power > 0:
                # power left to distribute
                if self.LOAD_STRAT == 'greedy':
                    p = power - used_power
                elif self.LOAD_STRAT == 'needy':
                    vehicle_energy_need = vehicle.get_delta_soc() / 100 * vehicle.battery.capacity
                    f = vehicle_energy_need / desired_energy_need
                    p = power * f
                elif self.LOAD_STRAT == 'balanced':
                    p = (power - used_power) / (len(needy_vehicles) - idx)

                # load with power p
                cs_id = vehicle.connected_charging_station
                cs = self.world_state.charging_stations.get(cs_id, None)
                if cs and p > cs.min_power and p > vehicle.vehicle_type.min_charging_power:
                    load = vehicle.battery.load(self.interval, p)
                    avg_power = load['avg_power']
                    charging_stations[cs_id] = avg_power
                    used_power += avg_power
                    max_energy_need -= load['soc_delta']/100 * vehicle.battery.capacity

        # distribute surplus
        surplus_power = power - used_power
        for idx, vehicle in enumerate(vehicles):
            if power - used_power > 0:
                # surplus power left to distribute
                p = 0

                if self.LOAD_STRAT == 'greedy':
                    p = power - used_power
                elif self.LOAD_STRAT == 'needy' and max_energy_need > 0:
                    delta_soc = 1.0 - vehicle.battery.soc/100
                    f = delta_soc * vehicle.battery.capacity / max_energy_need
                    p = f * surplus_power
                elif self.LOAD_STRAT == 'balanced':
                    p = (power - used_power) / (len(vehicles) - idx)

                # load with power p
                cs_id = vehicle.connected_charging_station
                cs = self.world_state.charging_stations.get(cs_id, None)
                if cs and p > cs.min_power and p > vehicle.vehicle_type.min_charging_power:
                    # find remaining power of CS
                    cs_remaining_power = cs.max_power - charging_stations.get(cs_id, 0)
                    p = min(cs_remaining_power, p)
                    if p < cs.min_power:
                        p = 0
                    avg_power = vehicle.battery.load(self.interval,p)['avg_power']
                    used_power += avg_power
                    try:
                        charging_stations[cs_id] += avg_power
                    except KeyError:
                        # CS may not be in result dict yet
                        charging_stations[cs_id] = avg_power
        return charging_stations
