from copy import deepcopy
import datetime

from src import events, util
from src.strategy import Strategy


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
        self.HORIZON = 24
        self.PRICE_THRESHOLD = 0.001  # EUR/kWh
        self.LOAD_STRAT = 'needy'  # greedy, needy, balanced, individual

        # init parent class Strategy. May override defaults
        super().__init__(constants, start_time, **kwargs)
        self.description = "inverse ({}) with {} hour horizon".format(self.LOAD_STRAT, self.HORIZON)

        # HORIZON -> number of intervals
        self.timesteps_ahead = int(datetime.timedelta(hours=self.HORIZON) / self.interval)

        # set order of vehicles to load
        if self.LOAD_STRAT == 'greedy':
            self.sort_key = lambda v: (v[1].estimated_time_of_departure, v[0])
        elif self.LOAD_STRAT == 'needy':
            self.sort_key = lambda v: (-v[1].get_delta_soc()*v[1].battery.capacity, v[0])
        elif self.LOAD_STRAT == 'balanced':
            self.sort_key = lambda v: v[0]  # order does not matter
        elif self.LOAD_STRAT == 'individual':
            self.sort_key = lambda v: (v[1].estimated_time_of_departure, v[0])
        else:
            raise NotImplementedError(self.LOAD_STRAT)

        # adjust foresight for vehicle and price events
        horizon_timedelta = datetime.timedelta(hours=self.HORIZON)
        changed = 0
        for event in self.events.vehicle_events + self.events.grid_operator_signals:
            old_signal_time = event.signal_time
            # make events known at least HORIZON hours in advance
            event.signal_time = min(event.signal_time, event.start_time - horizon_timedelta)
            # make sure events don't signal before start
            event.signal_time = max(event.signal_time, start_time)
            changed += event.signal_time < old_signal_time
        if changed:
            print(changed, "events signaled earlier")

        for vid, vehicle in self.world_state.vehicles.items():
            if vehicle.connected_charging_station is None and vehicle.get_delta_soc() > 0:
                print("Warning: {} starts not connected and with less SoC than desired".format(vid))

    def step(self, event_list=[]):
        super().step(event_list)

        # reset charging station power
        for cs in self.world_state.charging_stations.values():
            cs.current_power = 0

        # gather info about grid connectors
        gcs = {}
        for gc_id, gc in self.world_state.grid_connectors.items():
            gcs[gc_id] = {
                'vehicles': [],  # vehicles to be charged connected to this GC
                'v_info': [[]],  # vehicle info for each timestep
                'batteries': {},
                'ts': [],        # timestep infos
                'max_power': gc.cur_max_power,
                'feed_in': {k: -v for k, v in gc.current_loads.items() if v < 0},
                'costs': {
                    'min': util.get_cost(0, gc.cost),
                    'max': util.get_cost(gc.cur_max_power, gc.cost),
                    'cur': gc.cost
                }
            }
            vehicle_key_LUT = {}

        # sort vehicles
        sorted_vehicles = sorted(self.world_state.vehicles.items(), key=self.sort_key)
        # remember for each vehicle key the corresponding GC and index in vehicle array
        vehicle_key_LUT = {}
        # get connected vehicles
        for vid, vehicle in sorted_vehicles:
            cs_id = vehicle.connected_charging_station
            if cs_id is not None:
                cs = self.world_state.charging_stations[cs_id]
                gcs[cs.parent]['vehicles'].append(vehicle)

                # compute remaining standing time RST
                rst = vehicle.estimated_time_of_departure - self.current_time
                # number of remaining standing steps RSS
                rss = rst / self.interval
                # how much of RSS is within HORIZON?
                stand_frac = min(self.timesteps_ahead / rss, 1)

                gcs[cs.parent]["v_info"][0].append({
                    "soc_delta": 0,
                    "connected_charging_station": vehicle.connected_charging_station,
                    "desired_soc": vehicle.desired_soc * stand_frac,
                })
                # remember idx of vehicle by id
                vehicle_key_LUT[vid] = (gc_id, len(gcs[cs.parent]["v_info"][0]) - 1)

        # get connected batteries
        for bid, battery in self.world_state.batteries.items():
            gcs[battery.parent]["batteries"][bid] = battery

        # look ahead (limited by horizon)
        # get future events and predict external load and cost for each timestep
        # take note of vehicle arriving and leaving and their soc
        event_idx = 0

        cur_time = self.current_time - self.interval
        for timestep_idx in range(self.timesteps_ahead):
            cur_time += self.interval

            if timestep_idx > 0:
                # copy last vehicle info
                for gc_info in gcs.values():
                    new_vehicle_infos = deepcopy(gc_info["v_info"][-1])
                    # reset soc_delta for all vehicles
                    for info in new_vehicle_infos:
                        info["soc_delta"] = 0
                    gc_info["v_info"].append(new_vehicle_infos)

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
                    gcs[gc_id]['feed_in'][event.name] = event.value
                elif type(event) == events.VehicleEvent:
                    vid = event.vehicle_id
                    vehicle = self.world_state.vehicles[vid]
                    cs_id = vehicle.connected_charging_station
                    if cs_id is None:
                        # CS not set -> GC unknown
                        continue
                    gc_id, vidx = vehicle_key_LUT[vid]
                    if event.event_type == "departure":
                        gcs[gc_id]["v_info"][-1][vidx]["connected_charging_station"] = None
                    elif event.event_type == "arrival":
                        # compute remaining standing time
                        rst = (event.update["estimated_time_of_departure"] - cur_time)
                        # number of remaining standing steps
                        rss = rst / self.interval
                        # number of steps to HORIZON
                        remaining_steps = self.timesteps_ahead - timestep_idx
                        stand_frac = min(remaining_steps / rss, 1)
                        gcs[gc_id]["v_info"][-1][vidx].update(event.update)
                        gcs[gc_id]["v_info"][-1][vidx]["desired_soc"] *= stand_frac
                        new_cs_id = event.update["connected_charging_station"]
                        if new_cs_id:
                            new_gc_id = self.world_state.charging_stations[new_cs_id].parent
                            if gc_id != new_gc_id:
                                print("Operation not supported: \
                                      vehicle {} charges at different grid connectors".format(vid))
                                gcs[gc_id]["v_info"][-1][vidx]["connected_charging_station"] = None
            # end of useful events

            # compute available power and associated costs
            for gc_id, gc in self.world_state.grid_connectors.items():
                # get (predicted) external load
                if timestep_idx == 0:
                    # use actual external load
                    ext_load = gc.get_current_load()
                else:
                    ext_load = gc.get_avg_ext_load(cur_time, self.interval)
                    ext_load -= sum(gcs[gc_id]['feed_in'].values())
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
            # end of GC management
        # end of future events

        charging_stations = {}
        for gc_id, gc_info in gcs.items():
            gc = self.world_state.grid_connectors[gc_id]

            # retrieve min/max costs for this GC
            min_costs = gc_info['costs']['min']
            max_costs = gc_info['costs']['max']

            # compute cost for 1 kWh
            cost_for_one = util.get_cost(1, gc.cost)

            # is safe if price is below threshold
            safe = cost_for_one <= self.PRICE_THRESHOLD

            if safe:
                # cheap energy price
                # charge all connected vehicles with maximum power
                if self.LOAD_STRAT == 'individual':
                    # charge each vehicle with max power
                    for vehicle in gc_info["vehicles"]:
                        cs_id = vehicle.connected_charging_station
                        cs = self.world_state.charging_stations[cs_id]
                        power = gc.cur_max_power - gc.get_current_load()
                        power = util.clamp_power(power, vehicle, cs)
                        avg_power = vehicle.battery.load(self.interval, power)['avg_power']
                        charging_stations[cs_id] = gc.add_load(cs_id, avg_power)
                        cs.current_power += avg_power
                else:
                    power = gc.cur_max_power - gc.get_current_load()
                    charging_info = self.load_vehicles(gc_info["vehicles"], power)
                    # apply CS power
                    for cs_id, power in charging_info.items():
                        charging_stations[cs_id] = gc.add_load(cs_id, power)
                        cs.current_power += power

            else:
                # find minimum viable price to saturate all vehicles during HORIZON

                if self.LOAD_STRAT == 'individual':
                    # all vehicles independent
                    for vidx, vehicle in enumerate(gc_info["vehicles"]):
                        # check if sufficiently charged for all trips within HORIZON
                        sim_vehicle = deepcopy(vehicle)
                        safe = True
                        v_info = [i[vidx] for i in gc_info["v_info"]]
                        for cur_v_info in v_info:
                            sim_vehicle.battery.soc -= cur_v_info["soc_delta"]
                            safe &= sim_vehicle.battery.soc >= cur_v_info["desired_soc"] - self.EPS
                        if safe:
                            # don't need charging
                            continue

                        cs_id = vehicle.connected_charging_station
                        cs = self.world_state.charging_stations[cs_id]
                        min_power = max(cs.min_power, vehicle.vehicle_type.min_charging_power)
                        max_power = min(
                            cs.max_power,
                            vehicle.battery.loading_curve.max_power,
                            gc.cur_max_power - gc.get_current_load()
                        )
                        c_min_c = max(min_costs, util.get_cost(min_power, gc.cost))
                        c_max_c = min(max_costs, util.get_cost(max_power, gc.cost))
                        idx = 0
                        while (idx < self.ITERATIONS or not safe) and c_max_c - c_min_c > self.EPS:
                            idx += 1
                            # binary search: try out average of min and max
                            cur_cost = (c_max_c + c_min_c) / 2
                            # sim_time = self.current_time - self.interval
                            # reset vehicle SoC
                            sim_vehicle.battery.soc = vehicle.battery.soc
                            # simulate future timesteps
                            for ts_idx, ts_info in enumerate(gc_info["ts"]):
                                # sim_time += self.interval
                                cur_info = v_info[ts_idx]
                                cur_cs_id = cur_info["connected_charging_station"]
                                sim_vehicle.battery.soc -= cur_info["soc_delta"]
                                if cur_cs_id is None:
                                    # vehicle left
                                    if sim_vehicle.battery.soc < cur_info["desired_soc"] - self.EPS:
                                        # not enough charge
                                        safe = False
                                        c_min_c = cur_cost
                                        break
                                    continue
                                # cs = self.world_state.charging_stations[cur_cs_id]
                                # get power for cur_cost
                                power = util.get_power(cur_cost, ts_info["costs"])
                                power = power or ts_info['max'] - ts_info["ext"]
                                # power = util.clamp_power(power, sim_vehicle, cs)
                                # charge sim_vehicle with this power
                                sim_vehicle.battery.load(self.interval, power)
                            else:
                                # end of simulated time: check desired_soc
                                safe = sim_vehicle.battery.soc >= cur_info["desired_soc"] - self.EPS
                                if safe:
                                    # sufficiently charged
                                    c_max_c = cur_cost
                                else:
                                    # not enough charge at end of simulation
                                    c_min_c = cur_cost

                        # end of binary search
                        # charge for real
                        power = util.get_power(cur_cost, gc.cost)
                        power = power or gc.cur_max_power - gc.get_current_load()
                        # power already clamped due to min/max_power
                        avg_power = vehicle.battery.load(self.interval, power)['avg_power']
                        charging_stations[cs_id] = gc.add_load(cs_id, avg_power)
                        cs.current_power += avg_power
                    # next vehicle
                else:
                    power = None
                    idx = 0
                    sim_vehicles = deepcopy(gc_info["vehicles"])

                    # check if all vehicles can complete next trips
                    safe = True
                    for cur_info in gc_info["v_info"]:
                        for vidx, v_info in enumerate(cur_info):
                            sim_vehicles[vidx].battery.soc -= v_info["soc_delta"]
                            if sim_vehicles[vidx].battery.soc < v_info["desired_soc"] - self.EPS:
                                # can't complete next trip
                                safe = False
                                break
                        if not safe:
                            # last vehicle not charged enough
                            break
                    if safe:
                        # all vehicles charged: no action required
                        continue

                    # try to reach optimum cost level
                    # ... at least for ITERATIONS loops
                    # ... all vehicles must be loaded (safe result)
                    # ... optimum may converge -> min and max must be different
                    while (idx < self.ITERATIONS or not safe) and max_costs - min_costs > self.EPS:
                        idx += 1
                        # binary search: try out average of min and max
                        cur_costs = (max_costs + min_costs) / 2
                        # sim_time = self.current_time - self.interval

                        # reset vehicle SoC
                        for vidx, sim_vehicle in enumerate(sim_vehicles):
                            sim_vehicle.battery.soc = gc_info["vehicles"][vidx].battery.soc

                        # simulate next timesteps
                        for ts_idx, ts_info in enumerate(gc_info["ts"]):
                            # sim_time += self.interval
                            # update vehicle info from events
                            charging_vehicles = []
                            safe = True
                            for vidx, sim_vehicle in enumerate(sim_vehicles):
                                cur_v_info = gc_info["v_info"][ts_idx][vidx]
                                new_cs_id = cur_v_info["connected_charging_station"]
                                sim_vehicle.connected_charging_station = new_cs_id
                                sim_vehicle.battery.soc -= cur_v_info["soc_delta"]
                                sim_vehicle.desired_soc = cur_v_info["desired_soc"]
                                if sim_vehicle.connected_charging_station is None:
                                    if sim_vehicle.get_delta_soc() < self.EPS:
                                        # fail: at least one vehicle not charged enough when leaving
                                        safe = False
                                        break
                                else:
                                    charging_vehicles.append(sim_vehicle)
                            if not safe:
                                break
                            # get power from current costs
                            power = util.get_power(cur_costs, ts_info["costs"])
                            power = power or ts_info["max"]
                            power = power - ts_info['ext']
                            self.load_vehicles(charging_vehicles, power)
                        else:
                            # check at end of successful simulation that vehicles are charged enough
                            for sim_vehicle in sim_vehicles:
                                safe &= sim_vehicle.get_delta_soc() < self.EPS
                        # adjust costs based on result
                        if safe:
                            # all vehicles charged -> reduce costs
                            max_costs = cur_costs
                        else:
                            # some vehicles not charged -> increase costs
                            min_costs = cur_costs
                    # end of simulation
                    # get optimum power
                    power = util.get_power(cur_costs, gc.cost)
                    # power may be None (constant price)
                    power = power or gc.cur_max_power
                    # make sure power is within GC limits (should not be needed?)
                    power = min(power, gc.cur_max_power)
                    # subtract external loads
                    power -= gc.get_current_load()

                    # charge for real, get CS power values
                    charging_info = self.load_vehicles(gc_info['vehicles'], power)
                    # apply CS power
                    for cs_id, power in charging_info.items():
                        charging_stations[cs_id] = gc.add_load(cs_id, power)
                        cs.current_power += power
                # end of fleet simulation
            # end of non-optimal energy price
            # all vehicles loaded

            # distribute surplus power to vehicles
            # power is clamped to CS max_power
            for vehicle in gc_info['vehicles']:
                cs_id = vehicle.connected_charging_station
                cs = self.world_state.charging_stations[cs_id]
                if gc.get_current_load() < 0:
                    # surplus power
                    power = util.clamp_power(-gc.get_current_load(), vehicle, cs)
                    avg_power = vehicle.battery.load(self.interval, power)['avg_power']
                    charging_stations[cs_id] = gc.add_load(cs_id, avg_power)
                    cs.current_power += avg_power

            # charge/discharge batteries
            for b_id, battery in gc_info["batteries"].items():
                if util.get_cost(1, gc.cost) <= self.PRICE_THRESHOLD:
                    # low price: charge with full power
                    power = gc.cur_max_power - gc.get_current_load()
                    power = 0 if power < battery.min_charging_power else power
                    avg_power = battery.load(self.interval, power)['avg_power']
                    gc.add_load(b_id, avg_power)
                elif gc.get_current_load() < 0:
                    # surplus energy: charge
                    power = -gc.get_current_load()
                    power = 0 if power < battery.min_charging_power else power
                    avg_power = battery.load(self.interval, power)['avg_power']
                    gc.add_load(b_id, avg_power)
                else:
                    # GC draws power: use stored energy to support GC
                    bat_power = battery.unload(self.interval, gc.get_current_load())['avg_power']
                    gc.add_load(b_id, -bat_power)
            # end of batteries
        # end of GC

        return {'current_time': self.current_time, 'commands': charging_stations}

    def load_vehicles(self, vehicles, power):
        # load vehicles with specific strategy

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
                desired_energy_need += soc * vehicle.battery.capacity
            max_energy_need += (1 - soc) * vehicle.battery.capacity

        # load vehicles below desired SOC first
        used_power = 0
        for idx, vehicle in enumerate(needy_vehicles):
            if power - used_power > 0:
                # power left to distribute
                if self.LOAD_STRAT == 'greedy':
                    p = power - used_power
                elif self.LOAD_STRAT == 'needy':
                    vehicle_energy_need = vehicle.get_delta_soc() * vehicle.battery.capacity
                    f = vehicle_energy_need / desired_energy_need
                    p = power * f
                elif self.LOAD_STRAT == 'balanced':
                    p = (power - used_power) / (len(needy_vehicles) - idx)

                # load with power p
                cs_id = vehicle.connected_charging_station
                cs = self.world_state.charging_stations.get(cs_id, None)
                if cs and p > cs.min_power and p > vehicle.vehicle_type.min_charging_power:
                    p = min(cs.max_power, p)
                    if p < cs.min_power:
                        p = 0
                    load = vehicle.battery.load(self.interval, p)
                    avg_power = load['avg_power']
                    charging_stations[cs_id] = avg_power
                    used_power += avg_power
                    max_energy_need -= load['soc_delta'] * vehicle.battery.capacity

        # distribute surplus
        surplus_power = power - used_power
        for idx, vehicle in enumerate(vehicles):
            if power - used_power > 0:
                # surplus power left to distribute
                p = 0

                if self.LOAD_STRAT == 'greedy':
                    p = power - used_power
                elif self.LOAD_STRAT == 'needy' and max_energy_need > 0:
                    delta_soc = 1.0 - vehicle.battery.soc
                    f = delta_soc * vehicle.battery.capacity / max_energy_need
                    p = f * surplus_power
                elif self.LOAD_STRAT == 'balanced':
                    p = (power - used_power) / (len(vehicles) - idx)

                # load with power p
                cs_id = vehicle.connected_charging_station
                cs = self.world_state.charging_stations.get(cs_id, None)
                if cs and p > cs.min_power and p > vehicle.vehicle_type.min_charging_power:
                    # find remaining power of CS
                    # cannot exceed cs.max_power
                    cs_remaining_power = cs.max_power - charging_stations.get(cs_id, 0)
                    p = min(cs_remaining_power, p)
                    if p < cs.min_power:
                        p = 0
                    avg_power = vehicle.battery.load(self.interval, p)['avg_power']
                    used_power += avg_power
                    try:
                        charging_stations[cs_id] += avg_power
                    except KeyError:
                        # CS may not be in result dict yet
                        charging_stations[cs_id] = avg_power
        return charging_stations
