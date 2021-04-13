from copy import deepcopy
import datetime

from netz_elog import events, util
from netz_elog.strategy import Strategy


class Battery(Strategy):
    """
    Charging strategy that uses a battery to store power at times of low cost.
    """
    def __init__(self, constants, start_time, **kwargs):
        # minimum binary seach depth
        self.ITERATIONS = 12
        # compare close floating points
        self.EPS = 1e-5
        # fraction of SOC allowed less
        # eg. margin = 0.05: vehicles are allowed to leave with 95% of desired SOC
        self.SOC_MARGIN = 0.

        self.USE_COST = True

        self.LOAD_STRAT = 'needy' # greedy, needy, balanced

        # init parent class Strategy. May override defaults
        super().__init__(constants, start_time, **kwargs)
        self.description = "battery (constant {}, {})".format("cost" if self.USE_COST else "power", self.LOAD_STRAT)

        # set order of vehicles to load
        if self.LOAD_STRAT == 'greedy':
            self.sort_key=lambda v: v.estimated_time_of_departure
        elif self.LOAD_STRAT == 'needy':
            self.sort_key=lambda v: -v.get_delta_soc()*v.battery.capacity
        elif self.LOAD_STRAT == 'balanced':
            self.sort_key=lambda v: 0 # order does not matter
        else:
            raise NotImplementedError(self.LOAD_STRAT)

        # for now, only one grid connector allowed
        assert len(self.world_state.grid_connectors) == 1, "Need exactly one grid connector"
        # exactly one battery needed (more can be pooled, no batteries defeat purpose of strategy)
        assert len(self.world_state.batteries) == 1, "Need exactly one battery"

    def step(self, event_list=[]):
        super().step(event_list)

        # gather info of only grid connector
        gc = list(self.world_state.grid_connectors.values())[0]
        bat_id, battery = list(self.world_state.batteries.items())[0]
        timesteps = []
        max_power = gc.cur_max_power
        feed_in = 0

        min_cost = util.get_cost(0, gc.cost)
        max_cost = util.get_cost(gc.cur_max_power, gc.cost)
        cur_cost = gc.cost

        # get future events and predict external load and cost for each timestep
        event_idx = 0
        timesteps_per_day = int(datetime.timedelta(days=1) / self.interval)

        cur_time = self.current_time - self.interval
        # look at next 24h
        for timestep_idx in range(timesteps_per_day):
            cur_time += self.interval

            # peek into future events for external load or cost changes
            while True:
                try:
                    event = self.world_state.future_events[event_idx]
                except IndexError:
                    # no more events
                    break
                if event.start_time > cur_time:
                    # event after this timestep: stop peeking
                    break
                event_idx += 1
                if type(event) == events.GridOperatorSignal:
                    # update GC info
                    if gc.max_power:
                        event.max_power = event.max_power or gc.max_power
                        max_power = min(event.max_power, gc.max_power)
                    else:
                        max_power = event.max_power
                    cur_cost = event.cost
                elif type(event) == events.EnergyFeedIn:
                    feed_in = event.value

            # compute available power and associated costs
            # get (predicted) external load
            if timestep_idx == 0:
                # use actual external load (with feed-in)
                ext_load = gc.get_current_load()
            else:
                ext_load = gc.get_avg_ext_load(cur_time, self.interval)
                ext_load -= feed_in

             # new timestep info
            timesteps.append({
                'max': max_power,
                'ext': ext_load,
                'cost': cur_cost
            })

            # get cost for no power
            min_power_cost = util.get_cost(0, cur_cost)
            # get cost for max power
            max_power_cost = util.get_cost(max_power, cur_cost)
            # update min/max costs in GC info
            min_cost = min(min_cost, min_power_cost)
            max_cost = max(max_cost, max_power_cost)
        # end of 24h event prediction

        # compute cost for upper limit
        cur_max_cost = util.get_cost(gc.cur_max_power, gc.cost)

        # is safe if cost is negative (skip computation, use max power)
        safe = cur_max_cost <= 0

        if safe:
            # negative energy price
            # charge all connected vehicles, even above desired_soc
            cur_lvl = cur_max_cost if self.USE_COST else gc.cur_max_power
            # skip simulation
            min_lvl = cur_max_cost
            max_lvl = min_lvl
            # charge all vehicles, regardless of SOC
            vehicles= self.world_state.vehicles
        else:
            cur_lvl = None
            min_lvl = min_cost if self.USE_COST else 0
            max_lvl = max_cost if self.USE_COST else gc.cur_max_power
            # remove all vehicles from simulation where desired SOC is reached
            vehicles = {
                vid: v for vid,v in self.world_state.vehicles.items()
                if v.battery.soc < v.desired_soc
            }
            # copy vehicles and battery for simulation (don't change SOC of originals)
            sim_vehicles = deepcopy(vehicles)
            sim_battery = deepcopy(battery)

        idx = 0
        # try to reach optimum power level (minimum viable power)
        # ... at least for ITERATIONS loops
        # ... all vehicles must be loaded (safe result)
        # ... optimum may converge -> min and max must be different
        # ... price may be negative -> min >= max -> skip simulation
        # ... may be cost or power, depending on USE_COST flag
        while (idx < self.ITERATIONS or not safe) and max_lvl - min_lvl > self.EPS:
            idx += 1
            # binary search: try out average of min and max
            cur_lvl = (max_lvl + min_lvl) / 2
            sim_time  = self.current_time - self.interval

            # reset vehicle and battery SOC
            for vid, vehicle in sim_vehicles.items():
                vehicle.battery.soc = vehicles[vid].battery.soc
            sim_battery.soc = battery.soc

            # simulate next 24h
            for ts_info in timesteps:
                # ts_info fields: max (current max GC power), ext (current ext. load), cost
                sim_time += self.interval
                sim_charging = []

                # check that any vehicle in need of charging still has time
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
                    min_lvl = cur_lvl
                    break

                if len(sim_charging) == 0:
                    # all vehicles left, still valid result:
                    # decrease allowed costs
                    max_lvl = cur_lvl
                    break


                # still vehicles left to charge

                # get power level for next 24h
                ext_power  = ts_info['ext']
                bat_power  = sim_battery.get_available_power(self.interval)
                if self.USE_COST:
                    # how much energy can be loaded with current cost?
                    # cur_lvl SHOULD be achievable (ValueError otherwise)
                    grid_power = util.get_power(cur_lvl, ts_info['cost'])

                    # grid_power may be None (constant price)
                    # can charge with max_power then
                    grid_power = grid_power or ts_info['max']
                else:
                    # use same power over 24h
                    grid_power = cur_lvl

                charge_power = grid_power - ext_power + bat_power
                cs_info = self.load_vehicles(sim_charging, charge_power)
                used_power = sum(cs_info.values())

                # adjust simulated battery SOC
                if used_power + ext_power < grid_power:
                    # less power used than available: use surplus to charge battery
                    sim_battery.load(self.interval, grid_power - used_power - ext_power)
                else:
                    # extra power used: drain battery by difference
                    sim_battery.unload(self.interval, used_power + ext_power - grid_power)
                    used_bat_power = sim_battery.unload(self.interval, used_power + ext_power - grid_power)['avg_power']
                    assert bat_power >= used_bat_power

            # after all timesteps done: check all vehicles are loaded
            safe = True
            for vehicle in sim_vehicles.values():
                safe &= vehicle.battery.soc >= ((1.0 - self.SOC_MARGIN) * vehicle.desired_soc)
            # adjust costs based on result
            if safe:
                max_lvl = cur_lvl
            else:
                min_lvl = cur_lvl

        ext_power = gc.get_current_load()
        bat_power = battery.get_available_power(self.interval)
        # get optimum power
        if self.USE_COST:
            grid_power = util.get_power(cur_lvl, gc.cost)
            grid_power = grid_power or gc.cur_max_power
        else:
            grid_power = cur_lvl

        # make sure power is within GC limits (should not be needed?)
        grid_power = min(grid_power, gc.cur_max_power)
        charging_stations = {}
        charge_power = grid_power - ext_power + bat_power
        cs_info = self.load_vehicles(vehicles.values(), charge_power)
        for cs_id, power in cs_info.items():
            charging_stations[cs_id] = gc.add_load(cs_id, power)
        used_power = sum(cs_info.values())

        # adjust simulated battery SOC
        if used_power + ext_power < grid_power:
            # less power used than available: use surplus to charge battery
            bat_power = battery.load(self.interval, grid_power - used_power - ext_power)['avg_power']
        else:
            # extra power used: drain battery by difference
            bat_power = -battery.unload(self.interval, used_power + ext_power - grid_power)['avg_power']
            # this might not be enough, rest is taken from grid
        gc.add_load(bat_id, bat_power)

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
        for vehicle in needy_vehicles:
            if power - used_power > 0:
                # power left to distribute
                if self.LOAD_STRAT == 'greedy':
                    p = power - used_power
                elif self.LOAD_STRAT == 'needy':
                    vehicle_energy_need = vehicle.get_delta_soc() / 100 * vehicle.battery.capacity
                    f = vehicle_energy_need / desired_energy_need
                    p = power * f
                elif self.LOAD_STRAT == 'balanced':
                    p = power / len(needy_vehicles)

                # load with power p
                load = vehicle.battery.load(self.interval, p)
                avg_power = load['avg_power']
                cs_id = vehicle.connected_charging_station
                charging_stations[cs_id] = avg_power
                used_power += avg_power
                max_energy_need -= load['soc_delta']/100 * vehicle.battery.capacity

        # distribute surplus
        surplus_power = power - used_power
        for vehicle in vehicles:
            if power - used_power > 0:
                # surplus power left to distribute
                p = 0
                # find remaining power of CS
                cs_id = vehicle.connected_charging_station
                cs = self.world_state.charging_stations[cs_id]
                cs_remaining_power = cs.max_power - charging_stations.get(cs_id, 0)
                if self.LOAD_STRAT == 'greedy':
                    p = power - used_power
                elif self.LOAD_STRAT == 'needy' and max_energy_need > 0:
                    delta_soc = 1.0 - vehicle.battery.soc/100
                    f = delta_soc * vehicle.battery.capacity / max_energy_need
                    p = f * surplus_power
                elif self.LOAD_STRAT == 'balanced':
                    p = surplus_power / len(vehicles)

                # load with power p
                p = min(cs_remaining_power, p)
                avg_power = vehicle.battery.load(self.interval,p)['avg_power']
                used_power += avg_power
                try:
                    charging_stations[cs_id] += avg_power
                except KeyError:
                    # CS may not be in result dict yet
                    charging_stations[cs_id] = avg_power
        return charging_stations
