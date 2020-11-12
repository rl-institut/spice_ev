from copy import deepcopy
import datetime

import events
from strategy import Strategy
import util


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
        # low pass filter for predicted external cost
        self.LPF = 0.75
        # fraction of SOC allowed less
        # eg. margin = 0.05: vehicles are allowed to leave with 95% of desired SOC
        self.SOC_MARGIN = 0.0
        self.LOAD_STRAT = 'needy' # greedy, needy, balanced

        # init parent class Strategy. May override defaults
        super().__init__(constants, start_time, **kwargs)
        self.description = "inverse ({})".format(self.LOAD_STRAT)

        # prepare dictionary of predicted external load
        self.pred_ext_load = {}

        # initialize external load prediction for each timestep in a day
        timesteps_per_day = int(datetime.timedelta(days=1) / self.interval)
        cur_time = start_time
        for _ in range(timesteps_per_day):
            for gc in self.world_state.grid_connectors.keys():
                self.pred_ext_load[str(cur_time.time())] = {
                    gc: 0 for gc in self.world_state.grid_connectors.keys()
                }
            cur_time += self.interval

        # set order of vehicles to load
        if self.LOAD_STRAT == 'greedy':
            self.sort_key=lambda v: v.estimated_time_of_departure
        elif self.LOAD_STRAT == 'needy':
            self.sort_key=lambda v: -v.get_delta_soc()
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
        predicted_loads = self.pred_ext_load[timestamp]
        gcs = {}
        for gc_id, gc in self.world_state.grid_connectors.items():
            # update predicted external load
            predicted_load = predicted_loads[gc_id]
            actual_load = sum(gc.current_loads.values())
            predicted_loads[gc_id] += (actual_load - predicted_load) * self.LPF

            gcs[gc_id] = {
                'vehicles': {}, # vehicles to be charged connected to this GC
                'ts': [],       # timestep infos
                'max_power': gc.cur_max_power,
                'costs': {
                    'min': util.get_cost(0, gc.cost),
                    'max': util.get_cost(gc.cur_max_power, gc.cost),
                    'cur': gc.cost
                }
            }

        soc_need = 0
        # get connected vehicles
        for vid, vehicle in self.world_state.vehicles.items():
            cs_id = vehicle.connected_charging_station
            if cs_id is not None:
                cs = self.world_state.charging_stations[cs_id]
                gcs[cs.parent]['vehicles'][vid] = vehicle
                soc_need += max(vehicle.get_delta_soc(), 0)

        self.pred_ext_load[timestamp] = predicted_loads

        # look at next 24h
        # in this time, all vehicles must be charged
        # get future events and predict external load and cost for each timestep
        event_idx = 0
        timesteps_per_day = int(datetime.timedelta(days =1) / self.interval)

        cur_time = self.current_time - self.interval
        for _ in range(timesteps_per_day):
            cur_time += self.interval

            # still vehicles present at this timestep?
            vehicles_present = False
            for vehicle in self.world_state.vehicles.values():
                still_present  = vehicle.estimated_time_of_departure > cur_time and vehicle.connected_charging_station is not None
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

            # predicted external load
            cur_ext_load = self.pred_ext_load[str(cur_time.time())]

            # compute available power and associated costs
            for gc_id, gc in self.world_state.grid_connectors.items():
                # get cost for no power
                min_power_cost = util.get_cost(0, gcs[gc_id]['costs']['cur'])
                # get cost for max power
                max_power_cost = util.get_cost(gcs[gc_id]['max_power'], gcs[gc_id]['costs']['cur'])

                # new timestep info
                gcs[gc_id]['ts'].append({
                    'max': gcs[gc_id]['max_power'],
                    'ext': cur_ext_load[gc_id],
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

            min_costs = gc_info['costs']['min']
            max_costs = gc_info['costs']['max']

            cur_max_costs = util.get_cost(gc.cur_max_power, gc.cost)

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
                sim_need = soc_need

                # reset vehicle SOC
                for vid, sim_vehicle in sim_vehicles.items():
                    sim_vehicle.battery.soc = gc_info["vehicles"][vid].battery.soc

                # simulate next 24h
                for ts_info in gc_info['ts']:
                    sim_time += self.interval
                    sim_charging = []

                    safe = True
                    for vehicle in sorted(sim_vehicles.values(), key=self.sort_key):
                        needs_charging = vehicle.battery.soc < ((1.0 - self.SOC_MARGIN) * vehicle.desired_soc)
                        has_left = sim_time >= vehicle.estimated_time_of_departure
                        if needs_charging:
                            if has_left:
                                # fail: would have to leave with insufficient charge
                                safe = False
                                break
                            else:
                                # needs charging
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
                        last_valid = cur_costs
                        break


                    # still vehicles left to charge

                    # how much energy can be loaded with current cost?
                    # cur_costs SHOULD be achievable (ValueError otherwise)
                    max_power = util.get_power(cur_costs, ts_info['costs'])

                    # max_power may be None (constant price)
                    # can charge with max_power then
                    max_power = max_power or ts_info['max']
                    # subtract external loads
                    usable_power = max_power - ts_info['ext']
                    delta_sim_need = 0

                    for vehicle in sorted(sim_charging, key=self.sort_key):
                        if usable_power > 0:
                            if self.LOAD_STRAT == 'greedy':
                                # charge one vehicle after the other
                                avg_power = vehicle.battery.load(self.interval, usable_power)['avg_power']
                                usable_power -= avg_power
                            elif self.LOAD_STRAT == 'needy':
                                delta_soc = max(vehicle.get_delta_soc(), 0)
                                f = delta_soc / sim_need if sim_need > 0 else 0
                                delta_sim_need += vehicle.battery.load(self.interval, usable_power * f)['soc_delta']
                            elif self.LOAD_STRAT == 'balanced':
                                # distribute among remaining vehicles
                                vehicle.battery.load(self.interval, usable_power / len(sim_charging))

                    sim_need -= delta_sim_need

                # after all timesteps done: check all vehicles are loaded
                safe = True
                for vehicle in sim_charging:
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
            # subtract external loads
            usable_power = max_power - sum(gc.current_loads.values())
            # load according to LOAD_STRAT (same as in simulation)
            for vehicle in sorted(gc_info['vehicles'].values(), key=self.sort_key):
                if usable_power > 0:
                    if self.LOAD_STRAT == 'greedy':
                        # charge one vehicle after the other
                        avg_power = vehicle.battery.load(self.interval, usable_power)['avg_power']
                        usable_power -= avg_power
                    elif self.LOAD_STRAT == 'needy':
                        delta_soc = max(vehicle.get_delta_soc(), 0)
                        f = delta_soc / soc_need
                        avg_power = vehicle.battery.load(self.interval, usable_power * f)['avg_power']
                    elif self.LOAD_STRAT == 'balanced':
                        # distribute among remaining vehicles
                        avg_power = vehicle.battery.load(self.interval, usable_power / len(gc_info['vehicles']))['avg_power']
                    else:
                        raise NotImplementedError(self.LOAD_STRAT)
                    cs_id = vehicle.connected_charging_station
                    cs = self.world_state.charging_stations[cs_id]
                    charging_stations[cs_id] = gc.add_load(cs_id, avg_power)
                    cs.current_power += avg_power

        socs={vid: v.battery.soc for vid, v in self.world_state.vehicles.items()}
        # print(self.current_time, idx, sum([len(gcs[gc]['vehicles']) for gc in self.world_state.grid_connectors]), [len(gcs[gc]['ts']) for gc in self.world_state.grid_connectors], sum(socs.values())/len(socs))
        return {'current_time': self.current_time, 'commands': charging_stations, 'socs': socs}
