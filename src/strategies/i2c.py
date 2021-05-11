from copy import deepcopy
import datetime

from src import events, util
from src.strategy import Strategy


class I2c(Strategy):
    """
    Individual inverse charging strategy.
    Charging strategy that prioritizes times with lower power costs.
    Idea is to find minimum viable cost threshold over next 24h for each individual vehicle
    Timesteps with less external load and smaller costs are prioritized for loading
    """
    def __init__(self, constants, start_time, **kwargs):
        # defaults, can be overridden by CLO (through kwargs)

        # minimum binary seach depth
        self.ITERATIONS = 16
        # compare close floating points
        self.EPS = 1e-5

        self.PRICE_THRESHOLD = 0.1

        # init parent class Strategy. May override defaults
        super().__init__(constants, start_time, **kwargs)
        self.description = "inverse individual"

    def step(self, event_list=[]):
        super().step(event_list)

        # gather info about grid connectors
        timestamp = str(self.current_time.time())
        gcs = {}
        for gc_id, gc in self.world_state.grid_connectors.items():

            gcs[gc_id] = {
                'vehicles': {}, # vehicles to be charged connected to this GC
                # 'batteries': [],
                'ts': [],       # timestep infos
                'max_power': gc.cur_max_power,
                'feed_in': 0,
                'costs': {
                    'min': util.get_cost(0, gc.cost),
                    'max': util.get_cost(gc.cur_max_power, gc.cost),
                    'cur': gc.cost
                }
            }

        # for bat_id, bat in self.world_state.batteries.items():
            # gc_info[bat.parent]["batteries"].append(bat_id)

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
                    'costs': gcs[gc_id]['costs']['cur'],
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
        # find minimum cost -> minimal viable power per vehicle so it is charged when leaving or after 24h
        for gc_id, gc_info in gcs.items():
            gc = self.world_state.grid_connectors[gc_id]

            # retrieve min/max costs for this GC
            min_costs = gc_info['costs']['min']
            max_costs = gc_info['costs']['max']

            # compute cost for one kWh
            cur_cost_one = util.get_cost(1, gc.cost)

            # order vehicles by time needed
            vehicles = sorted(gc_info["vehicles"].values(), key=lambda v: v.battery.capacity*v.battery.soc/self.world_state.charging_stations[v.connected_charging_station].max_power)

            if cur_cost_one <= self.PRICE_THRESHOLD:
                # negative or no cost: charge as much as possible
                # give most energy to vehicles that need most
                # nah, just greedy (easier for simulation)

                # total_energy_needed = sum([v.battery.capacity * v.battery.soc / 100 for v in vehicles])

                # get maximum available power
                # current load might be negative (feed-in)
                gc_power = gc.cur_max_power - gc.get_current_load()
                charging_stations = {}

                for vehicle in vehicles:
                    cs_id = vehicle.connected_charging_station
                    cs = self.world_state.charging_stations[cs_id]
                    """
                    delta_soc = 1.0 - vehicle.battery.soc/100
                    # get fraction of needed energy from total energy
                    f = delta_soc * vehicle.battery.capacity / total_energy_needed
                    p = f * gc_power
                    """
                    # charge with maximum power
                    p = gc.cur_max_power - gc.get_current_load()
                    p = util.clamp_power(p, vehicle, cs)

                    # load
                    avg_power = vehicle.battery.load(self.interval,p)['avg_power']
                    charging_stations[cs_id] = gc.add_load(cs_id, avg_power)
                    cs.current_power += avg_power

            else:
                # cost above threshold: find minimum viable price for each vehicle
                for vehicle in vehicles:
                    if vehicle.battery.soc >= vehicle.desired_soc:
                        # desired SOC reached: no charging needed
                        continue

                    cs_id = vehicle.connected_charging_station
                    cs = self.world_state.charging_stations[cs_id]

                    sim_vehicle = deepcopy(vehicle)
                    idx = 0
                    safe = False
                    cur_max = max_costs
                    cur_min = min_costs
                    cur_costs = max_costs
                    while (idx < self.ITERATIONS or not safe) and cur_max - cur_min > self.EPS:
                        idx += 1
                        # binary search: try out average of min and max
                        cur_costs = (cur_max + cur_min) / 2
                        sim_time  = self.current_time - self.interval
                        # reset vehicle SOC
                        sim_vehicle.battery.soc = vehicle.battery.soc

                        # simulate next 24h
                        for ts_info in gc_info["ts"]:
                            sim_time += self.interval

                            if sim_time >= vehicle.estimated_time_of_departure:
                                # stop charging when vehicle has left CS
                                break

                            # charge

                            # is price below threshold?
                            cur_cost_one = util.get_cost(1, ts_info['costs'])
                            if cur_cost_one < self.PRICE_THRESHOLD:
                                # below treshold: charge with maximum power
                                p = ts_info['max'] - ts_info['ext']
                                p = util.clamp_power(p, sim_vehicle, cs)

                                # load
                                avg_power = sim_vehicle.battery.load(self.interval,p)
                            elif sim_vehicle.battery.soc < vehicle.desired_soc:
                                # price above threshold, sim_vehicle needs charging
                                # how much energy can be loaded with current cost?
                                # cur_costs SHOULD be achievable (ValueError otherwise)
                                p = util.get_power(cur_costs, ts_info['costs'])

                                # power may be None (constant price)
                                # can charge with max_power then
                                p = p or ts_info['max'] - ts_info['ext']
                                p = util.clamp_power(p, sim_vehicle, cs)
                                sim_vehicle.battery.load(self.interval,p)
                            # end of simulated timestep

                        # test that vehicle has charged enough when leaving CS
                        safe = sim_vehicle.battery.soc >= vehicle.desired_soc
                        if safe:
                            cur_max = cur_costs
                        else:
                            cur_min = cur_costs
                        # end of binary search

                    # cost must be above threshold
                    p = util.get_power(cur_costs, gc.cost)
                    p = p or gc.cur_max_power - gc.current_load()
                    p = util.clamp_power(p, vehicle, cs)
                    avg_power = vehicle.battery.load(self.interval, p)['avg_power']
                    charging_stations[cs_id] = gc.add_load(cs_id, avg_power)

        socs={vid: v.battery.soc for vid, v in self.world_state.vehicles.items()}
        return {'current_time': self.current_time, 'commands': charging_stations, 'socs': socs}
