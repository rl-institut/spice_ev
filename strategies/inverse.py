from copy import deepcopy
import datetime
from math import inf

import events
from strategy import Strategy
import util


class Inverse(Strategy):
    """
    Charging strategy that prioritizes times with lower power costs.
    """
    def __init__(self, constants, start_time, interval):
        super().__init__(constants, start_time, interval)
        self.description = "inverse"

        # prepare dictionary of predicted external load
        self.pred_ext_load = {}

        timesteps_per_day = int(datetime.timedelta(days=1) / interval)
        cur_time = start_time
        for _ in range(timesteps_per_day):
            for gc in self.world_state.grid_connectors.keys():
                self.pred_ext_load[str(cur_time.time())] = {
                    gc: 0 for gc in self.world_state.grid_connectors.keys()
                }
            cur_time += interval

        self.ITERATIONS = 16
        self.EPS = 1e-5

    def step(self, event_list=[]):
        super().step(event_list)

        # gather info about grid connectors
        timestamp = str(self.current_time.time())
        predicted_loads = self.pred_ext_load[timestamp]
        gcs = {}
        for gc_id, gc in self.world_state.grid_connectors.items():
            # update predicted external load
            predicted_load = predicted_loads[gc_id]
            actual_load = sum(gc.current_loads.values())
            predicted_loads[gc_id] = 0.0 * predicted_load + 1.0 * actual_load

            gcs[gc_id] = {
                'vehicles': {},
                'ts': [],
                'max_power': gc.cur_max_power,
                'costs': {
                    'min': inf,
                    'max': -inf,
                    'cur': gc.cost
                }
            }

            # get connected vehicles
            for vid, vehicle in self.world_state.vehicles.items():
                cs_id = vehicle.connected_charging_station
                delta_soc = vehicle.desired_soc - vehicle.battery.soc
                if cs_id and delta_soc > 0:
                    cs = self.world_state.charging_stations[cs_id]
                    gcs[cs.parent]['vehicles'][vid] = vehicle

        self.pred_ext_load[timestamp] = predicted_loads

        # reset charging station power
        # for cs in self.world_state.charging_stations.values():
            # cs.current_power = 0

        # set external load and prices until all vehicles gone (24h max)
        event_idx = 0
        timesteps_per_day = int(datetime.timedelta(days =1) / self.interval)
        timesteps_per_hour=     datetime.timedelta(hours=1) / self.interval

        cur_time = self.current_time - self.interval
        for _ in range(timesteps_per_day):
            cur_time += self.interval

            vehicles_charging = False
            for vehicle in self.world_state.vehicles.values():
                needs_charging = vehicle.battery.soc < vehicle.desired_soc
                still_present  = vehicle.estimated_time_of_departure > cur_time and vehicle.connected_charging_station is not None
                if still_present and needs_charging:
                    vehicles_charging = True
                    break

            if not vehicles_charging:
                break

            # peek into future events for external load or cost changes
            # for event in self.world_state.future_events:
            while True:
                try:
                    event = self.world_state.future_events[event_idx]
                except IndexError:
                    break
                if event.start_time > cur_time:
                    break
                event_idx += 1
                if type(event) == events.GridOperatorSignal:
                    gc_id = event.grid_connector_id
                    max_power = event.max_power or gcs[gc_id]['max_power']
                    gcs[gc_id]['max_power'] = min(gcs[gc_id]['max_power'], max_power)
                    gcs[gc_id]['costs']['cur'] = event.cost

            # predicted external load
            cur_ext_load = self.pred_ext_load[str(cur_time.time())]

            # compute available power and associated costs
            for gc_id, gc in self.world_state.grid_connectors.items():
                min_power_cost = util.get_cost(0, gcs[gc_id]['costs']['cur'])
                max_power_cost = util.get_cost(gcs[gc_id]['max_power'], gcs[gc_id]['costs']['cur'])

                gcs[gc_id]['ts'].append({
                    'max': gcs[gc_id]['max_power'],
                    'ext': cur_ext_load[gc_id],
                    'costs': gcs[gc_id]['costs']['cur']
                })
                old_min = gcs[gc_id]['costs']['min']
                old_max = gcs[gc_id]['costs']['max']
                gcs[gc_id]['costs']['min'] = min(old_min, min_power_cost, max_power_cost)
                gcs[gc_id]['costs']['max'] = max(old_max, min_power_cost, max_power_cost)

        if sum(len(gc['ts']) for gc in gcs.values()) == 0:
            # no charging -> no timesteps
            socs={vid: v.battery.soc for vid, v in self.world_state.vehicles.items()}
            return {'current_time': self.current_time, 'commands': {}, 'socs': socs}

        charging_stations = {}
        # find minimum viable power per grid connector
        # print("next step", self.current_time)
        for gc_id, gc_info in gcs.items():
            gc = self.world_state.grid_connectors[gc_id]

            min_costs = gc_info['costs']['min']
            max_costs = gc_info['costs']['max']

            safe = False
            last_valid = None

            idx = 0
            while (idx < self.ITERATIONS or not safe) and max_costs - min_costs > self.EPS:
                idx += 1
                cur_costs = (max_costs + min_costs) / 2
                sim_time  = self.current_time - self.interval
                sim_vehicles = deepcopy(gc_info["vehicles"])
                # print("next idx", idx, ':', min_costs, cur_costs, max_costs)
                for ts_info in gc_info['ts']:
                    sim_time += self.interval
                    valid = True
                    for vid in list(sim_vehicles.keys()):
                        vehicle = sim_vehicles[vid]
                        if vehicle.battery.soc >= vehicle.desired_soc:
                            # reached desired SOC: remove from list of vehicles to charge
                            # print(vid, "reached SOC at", sim_time)
                            del sim_vehicles[vid]
                        if sim_time >= vehicle.estimated_time_of_departure:
                            if vehicle.battery.soc < vehicle.desired_soc * 0.95:
                                # fail: would have to leave with insufficient charge
                                # print("{} not charged at {} ({} < {}, {})".format(vid, sim_time, vehicle.battery.soc, vehicle.desired_soc, vehicle.estimated_time_of_departure))
                                valid = False
                                break

                    # print("next ts", sim_time, len(sim_vehicles))

                    if not valid:
                        # at least one vehicle not charged in time:
                        # increase allowed costs
                        min_costs = cur_costs
                        safe = False
                        break

                    if len(sim_vehicles) == 0:
                        # all vehicles left, still valid result:
                        # decrease allowed costs
                        max_costs = cur_costs
                        safe = True
                        last_valid = cur_costs
                        break


                    # still vehicles left to charge

                    # how much energy can be loaded with current cost?
                    try:
                        max_power = util.get_power(cur_costs, ts_info['costs'])
                        # print(sum([v.desired_soc - v.battery.soc for v in sim_vehicles.values()]) /len(sim_vehicles))
                    except ValueError:
                        # oops, cost not achievable: reuse last
                        print("Math error")
                        safe = False
                        min_costs = last_valid
                        max_costs = last_valid
                        cur_costs = last_valid
                        break

                    # max_power may be None (constant price)
                    max_power = max_power or ts_info['max']
                    # subtract external loads
                    usable_power = max_power - ts_info['ext']

                    # no vehicle to grid
                    if usable_power < 0:
                        safe = False
                        min_costs = cur_costs
                        break

                    # distribute among remaining vehicles
                    avg_power = usable_power / len(sim_vehicles)
                    for vehicle in sim_vehicles.values():
                        vehicle.battery.load(self.interval, avg_power)

                    # load vehicles
                    # for vid in sorted(sim_vehicles):
                        # vehicle = sim_vehicles[vid]
                        # avg_power = vehicle.battery.load(self.interval, usable_power)['avg_power']
                        # usable_power -= avg_power

                # after all timesteps done: check all vehicles are loaded
                if len(sim_vehicles) > 0:
                    # at least one vehicle not charged in time:
                    # increase allowed costs
                    min_costs = cur_costs
                    safe = False

            # print(self.current_time, min_costs, cur_costs, max_costs, idx)
            max_power = util.get_power(cur_costs, gc.cost)
            # max_power may be None (constant price)
            max_power = max_power or gc.cur_max_power
            # subtract external loads
            usable_power = max_power - sum(gc.current_loads.values())
            # print(gc_info['ts'][0]['max'], gc.cur_max_power)
            # print(gc_info['ts'][0]['ext'], sum(gc.current_loads.values()))
            # print(max_power, usable_power)

            for vid in sorted(gc_info['vehicles']):
                vehicle = self.world_state.vehicles[vid]
                avg_power = vehicle.battery.load(self.interval, usable_power)['avg_power']
                usable_power -= avg_power
                cs_id = vehicle.connected_charging_station
                cs = self.world_state.charging_stations[cs_id]
                charging_stations[cs_id] = gc.add_load(cs_id, avg_power)
                cs.current_power += avg_power

        socs={vid: v.battery.soc for vid, v in self.world_state.vehicles.items()}
        return {'current_time': self.current_time, 'commands': charging_stations, 'socs': socs}
