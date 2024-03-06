from copy import deepcopy
import datetime
from warnings import warn

from spice_ev import util, events
from spice_ev.strategy import Strategy


class GridCheckedMarket(Strategy):
    """
    Charge at times of low price, but take grid capacity into account.

    Assumption: no V2G, no stationary batteries.
    """
    def __init__(self, components, start_time, **kwargs):
        self.HORIZON = 24
        super().__init__(components, start_time, **kwargs)
        self.description = "grid checked market"
        self.HORIZON = datetime.timedelta(hours=self.HORIZON)
        self.warn_capacity = set()  # remember GC where capacity became None

        # adjust foresight for grid operator events: all known at start
        for event in self.events.grid_operator_signals:
            event.signal_time = start_time

    def step(self):
        """ Calculate charging power in each timestep.

        :return: current time and commands of the charging stations
        :rtype: dict
        """
        commands = dict()
        # reset charging station power (nothing charged yet in this timestep)
        for cs in self.world_state.charging_stations.values():
            cs.current_power = 0
        for gc_id, gc in self.world_state.grid_connectors.items():
            commands.update(self.step_gc(gc_id, gc))
        return {'current_time': self.current_time, 'commands': commands}

    def step_gc(self, gc_id, gc):
        charging_stations = {}
        # filter vehicles that are charging at this GC
        vehicles = {
            vid: v for vid, v in self.world_state.vehicles.items()
            if v.connected_charging_station is not None
            and self.world_state.charging_stations[v.connected_charging_station].parent == gc_id
        }

        if not vehicles:
            # no vehicles, nothing to do (no batteries)
            return {}

        if gc.capacity is None and gc_id not in self.warn_capacity:
            # warn once if GC has grid capacity not set
            warn(f"{self.current_time}: {gc_id} has no associated grid capacity. "
                 "Later occurences ignored.", stacklevel=100)
            self.warn_capacity.add(gc_id)

        # order vehicles by time of departure
        vehicles = sorted(
            [(vid, v) for (vid, v) in vehicles.items()],
            key=lambda x: (x[1].estimated_time_of_departure or self.current_time, x[0]))

        # forecast: until last vehicle left, but no more than HORIZON
        last_departure = vehicles[-1][1].estimated_time_of_departure
        # ceil
        last_departure_idx = -((self.current_time - last_departure) // self.interval)
        timesteps_ahead = self.HORIZON // self.interval
        timesteps_ahead = min(timesteps_ahead, last_departure_idx)

        cur_cost = util.get_cost(1, gc.cost)
        cur_capacity = gc.capacity if gc.capacity is not None else gc.cur_max_power
        cur_max_power = gc.cur_max_power
        cur_local_generation = {k: -v for k, v in gc.current_loads.items() if v < 0}

        # ---------- GET NEXT EVENTS ---------- #
        timesteps = []

        # look ahead (limited by horizon)
        # get future events and predict fixed load and cost for each timestep
        event_idx = 0

        cur_time = self.current_time - self.interval
        for timestep_idx in range(timesteps_ahead):
            cur_time += self.interval

            # peek into future events
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
                if type(event) is events.GridOperatorSignal:
                    if event.grid_connector_id != gc_id:
                        continue
                    # update GC info
                    if event.max_power is not None:
                        cur_max_power = event.max_power
                    if event.cost is not None:
                        cur_cost = util.get_cost(1, event.cost)
                    if event.capacity is not None:
                        cur_capacity = event.capacity
                elif type(event) is events.LocalEnergyGeneration:
                    if event.grid_connector_id != gc_id:
                        continue
                    cur_local_generation[event.name] = event.value
                # vehicle events ignored (use vehicle info such as estimated_time_of_departure)

            # compute available power and associated costs
            # get (predicted) fixed load
            if timestep_idx == 0:
                # use actual fixed load
                fixed_load = gc.get_current_load()
            else:
                fixed_load = gc.get_avg_fixed_load(cur_time, self.interval) \
                           - sum(cur_local_generation.values())
            timesteps.append({
                "power": fixed_load,
                "max_power": cur_max_power,
                "capacity": min(cur_capacity, cur_max_power),
                "cost": cur_cost,
            })

        # ---------- ITERATE OVER VEHICLES ---------- #

        for vid, vehicle in vehicles:
            cs_id = vehicle.connected_charging_station
            cs = self.world_state.charging_stations[cs_id]

            # balance load over times with same cost
            # GC power must not exceed capacity

            # get timestep index where vehicle leaves (round up)
            if vehicle.estimated_time_of_departure is None:
                ts_leave = 1
            else:
                ts_leave = -((self.current_time-vehicle.estimated_time_of_departure)//self.interval)
                # est. time of departure might be in the past, but need at least one timestep
                ts_leave = max(ts_leave, 1)
            # get timesteps where vehicle is present
            vehicle_ts = timesteps[:ts_leave]
            # sort remaining timesteps by price and index
            sorted_ts = sorted((e["cost"], idx) for idx, e in enumerate(vehicle_ts))

            sim_vehicle = deepcopy(vehicle)
            power = [0] * len(sorted_ts)

            # iterate timesteps by order of the cheapest price to reach desired soc
            sorted_idx = 0
            while sorted_idx < len(sorted_ts):
                cost, start_idx = sorted_ts[sorted_idx]

                if sim_vehicle.battery.soc >= vehicle.desired_soc:
                    # desired SoC reached: no more charging needed
                    break

                # find timesteps with same price
                # prices below threshold are seen as same
                same_price_ts = [start_idx]
                # peek into next sorted: same price?
                same_sorted_price_idx = sorted_idx + 1
                while same_sorted_price_idx < len(sorted_ts):
                    next_cost, next_ts_idx = sorted_ts[same_sorted_price_idx]
                    if abs(next_cost - cost) < self.EPS:
                        same_sorted_price_idx += 1
                        same_price_ts.append(next_ts_idx)
                    else:
                        break

                # prepare ts with next highest price
                sorted_idx = same_sorted_price_idx

                # naive: charge with full power (up to capacity) during all timesteps
                old_soc = sim_vehicle.battery.soc
                for ts_idx in same_price_ts:
                    avail_power = timesteps[ts_idx]["capacity"] - timesteps[ts_idx]["power"]
                    p = util.clamp_power(avail_power, vehicle, cs)
                    avg_power = sim_vehicle.battery.load(self.interval, max_power=p)["avg_power"]
                    power[ts_idx] = avg_power

                if sim_vehicle.battery.soc >= vehicle.desired_soc:
                    # above desired SoC: find optimum power
                    min_power = 0
                    max_power = cs.max_power
                    safe = False

                    # should not lead to infinite loop, because
                    # 1) min_power and max_power converge
                    # 2) if not safe, power gets increased towards cs.max_power or last safe value
                    #    because naive version (with at most cs.max_power)
                    #    did overcharge, a suitable power should always exist
                    while not safe or max_power - min_power > self.EPS:
                        # reset SoC
                        sim_vehicle.battery.soc = old_soc
                        cur_power = (max_power + min_power) / 2
                        for ts_idx in same_price_ts:
                            avail_power = timesteps[ts_idx]["capacity"] - timesteps[ts_idx]["power"]
                            p = min(avail_power, cur_power)
                            p = util.clamp_power(p, vehicle, cs)
                            power[ts_idx] = sim_vehicle.battery.load(
                                self.interval, target_power=p)["avg_power"]
                        safe = sim_vehicle.battery.soc >= vehicle.desired_soc
                        if not safe:
                            # not charged enough
                            min_power = cur_power
                        else:
                            max_power = cur_power

            energy_needed = sim_vehicle.get_energy_needed()
            if energy_needed > 0:
                # reaching desired SoC not possible, maybe because of capacity restriction:
                # raise capacity evenly to reach desired SoC
                sim_vehicle.battery.soc = old_soc
                for ts_idx, ts_info in enumerate(sorted_ts):
                    ts_remaining = ts_leave - ts_idx
                    avail_power = timesteps[ts_idx]["max_power"] - timesteps[ts_idx]["power"]
                    # use prior power (within capacity) + needed fraction
                    p = power[ts_idx] + (energy_needed / ts_remaining) * self.ts_per_hour
                    p = min(avail_power, p)
                    p = util.clamp_power(p, vehicle, cs)
                    avg_power = sim_vehicle.battery.load(self.interval, target_power=p)["avg_power"]
                    power[ts_idx] = avg_power
                    energy_needed = sim_vehicle.get_energy_needed()

            # take note of final needed charging power, update timestep power infos
            cs.current_power = power[0]
            for cur_idx, cur_power in enumerate(power):
                timesteps[cur_idx]["power"] += cur_power

        # ---------- DISTRIBUTE SURPLUS ---------- #
        # all vehicles simulated
        # distribute surplus power to vehicles
        # no surplus: use prior calculated power
        for vid, vehicle in vehicles:
            cs_id = vehicle.connected_charging_station
            cs = self.world_state.charging_stations[cs_id]
            power = 0
            if timesteps[0]["power"] >= 0 and cs.current_power > 0:
                # no surplus: charge as intended
                power = cs.current_power
            elif timesteps[0]["power"] < -self.EPS:
                # surplus: charge greedy
                avail_power = -timesteps[0]["power"]
                power = cs.current_power + avail_power
                power = util.clamp_power(power, vehicle, cs)
            if power:
                # charge for real (apply power)
                avg_power = vehicle.battery.load(self.interval, target_power=power)['avg_power']
                charging_stations[cs_id] = gc.add_load(cs_id, avg_power)
                timesteps[0]["power"] += (avg_power - cs.current_power)

        if gc.capacity is not None and gc.get_current_load() > gc.capacity + 1e-3:
            print(f"{self.current_time}: {gc_id} capacity exceeded")

        return charging_stations
