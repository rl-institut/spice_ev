from copy import deepcopy
import datetime

from spice_ev import events, util
from spice_ev.strategy import Strategy


class BalancedMarket(Strategy):
    """ Price oriented charging at times of low energy price. """
    def __init__(self, components, start_time, **kwargs):
        self.PRICE_THRESHOLD = 0.001  # EUR/kWh
        self.HORIZON = 24  # maximum number of hours ahead

        super().__init__(components, start_time, **kwargs)
        assert len(self.world_state.grid_connectors) == 1, "Only one grid connector supported"
        self.description = "balanced (market-oriented)"

        # adjust foresight for price events
        horizon_timedelta = datetime.timedelta(hours=self.HORIZON)
        changed = 0
        for event in self.events.grid_operator_signals:
            old_signal_time = event.signal_time
            # make price events known at least HORIZON hours in advance
            event.signal_time = min(event.signal_time, event.start_time - horizon_timedelta)
            # make sure events don't signal before start
            event.signal_time = max(event.signal_time, start_time)
            changed += event.signal_time < old_signal_time
        if changed:
            print(changed, "events signaled earlier")

    def step(self):
        """ Calculate charging power in each timestep.

        :return: current time and commands of the charging stations
        :rtype: dict
        """

        gc = list(self.world_state.grid_connectors.values())[0]

        # dict to hold charging commands
        charging_stations = {}
        # list including ID of all V2G charging stations, used to compute remaining GC power
        discharging_stations = []
        # reset charging station power (nothing charged yet in this timestep)
        for cs in self.world_state.charging_stations.values():
            cs.current_power = 0

        # order vehicles by time of departure
        vehicles = sorted(
            [(vid, v) for (vid, v) in self.world_state.vehicles.items()
                if v.connected_charging_station is not None],
            key=lambda x: (x[1].estimated_time_of_departure, x[0]))

        cur_cost = gc.cost
        cur_local_generation = {k: -v for k, v in gc.current_loads.items() if v < 0}
        cur_max_power = gc.cur_max_power

        # ---------- GET NEXT EVENTS ---------- #
        timesteps = []

        # look ahead (limited by horizon)
        # get future events and predict fixed load and cost for each timestep
        event_idx = 0
        timesteps_ahead = int(datetime.timedelta(hours=self.HORIZON) / self.interval)

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
                    # update GC info
                    if event.max_power is not None:
                        cur_max_power = event.max_power
                    if event.cost is not None:
                        cur_cost = event.cost
                elif type(event) is events.LocalEnergyGeneration:
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
                "power": cur_max_power - fixed_load,
                "max_power": cur_max_power,
                "cost": cur_cost,
            })

        # order timesteps by cost for 1 kWh

        # ---------- ITERATE OVER VEHICLES ---------- #

        for vid, vehicle in vehicles:
            cs_id = vehicle.connected_charging_station
            cs = self.world_state.charging_stations[cs_id]
            original_soc = vehicle.battery.soc

            # balance load over times with same cost

            # get timestep index where vehicle leaves (round down)
            ts_leave = (vehicle.estimated_time_of_departure - self.current_time) // self.interval
            # get timesteps where vehicle is present
            vehicle_ts = timesteps[:ts_leave]
            # sort remaining timesteps by price and index
            sorted_ts = sorted(
                (util.get_cost(1, e["cost"]), idx)
                for idx, e in enumerate(vehicle_ts))

            sim_vehicle = deepcopy(vehicle)
            power = [0] * len(sorted_ts)

            # iterate timesteps by order of the cheapest price to reach desired soc
            sorted_idx = 0
            while sorted_idx < len(sorted_ts):
                cost, start_idx = sorted_ts[sorted_idx]

                # when below threshold, try to fill battery (still balanced charging)
                desired_soc = 1 if cost < self.PRICE_THRESHOLD else vehicle.desired_soc
                desired_soc -= self.EPS

                if sim_vehicle.battery.soc >= desired_soc:
                    # desired SoC reached: no more charging needed.
                    # don't block time steps for v2g if balanced charging
                    # does not occur in current TS
                    sorted_idx = 0
                    break

                # find timesteps with same price
                # prices below threshold are seen as same
                same_price_ts = [start_idx]
                # peek into next sorted: same price?
                same_sorted_price_idx = sorted_idx + 1
                while same_sorted_price_idx < len(sorted_ts):
                    next_cost, next_ts_idx = sorted_ts[same_sorted_price_idx]
                    if abs(next_cost - cost) < self.EPS or next_cost <= self.PRICE_THRESHOLD:
                        same_sorted_price_idx += 1
                        same_price_ts.append(next_ts_idx)
                    else:
                        break

                # prepare ts with next highest price
                sorted_idx = same_sorted_price_idx

                # naive: charge with full power during all timesteps
                old_soc = sim_vehicle.battery.soc
                for ts_idx in same_price_ts:
                    p = timesteps[ts_idx]["power"]
                    p = util.clamp_power(p, vehicle, cs)
                    power[ts_idx] = p
                    sim_vehicle.battery.load(self.interval, max_power=p)

                if sim_vehicle.battery.soc >= desired_soc:
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
                            p = min(timesteps[ts_idx]["power"], cur_power)
                            p = util.clamp_power(p, vehicle, cs)
                            power[ts_idx] = p
                            sim_vehicle.battery.load(self.interval, target_power=p)
                        safe = sim_vehicle.battery.soc >= desired_soc
                        if not safe:
                            # not charged enough
                            min_power = cur_power
                        else:
                            max_power = cur_power

                if start_idx == 0 and power[0]:
                    # current timestep: charge vehicle for real
                    p = power[0]
                    avg_power = vehicle.battery.load(self.interval, target_power=p)['avg_power']
                    charging_stations[cs_id] = gc.add_load(cs_id, avg_power)
                    cs.current_power += avg_power
                    # don't have to simulate further
                    break

            # normal charging done

            # ---------- VEHICLE TO GRID ---------- #

            # begin vehicle-to-grid/home at time with the highest price
            # and stop once it reaches charging timestep
            # off-by-one: v2g_sorted_idx is immediately decreased by one
            v2g_sorted_idx = len(sorted_ts)
            while vehicle.vehicle_type.v2g and v2g_sorted_idx > sorted_idx:
                sim_power = None
                v2g_sorted_idx -= 1
                v2g_cost, v2g_ts_idx = sorted_ts[v2g_sorted_idx]
                if v2g_cost < self.PRICE_THRESHOLD:
                    # too cheap for discharging energy: don't
                    break

                # save current states for backtracking
                old_power = deepcopy(power)
                old_sorted_idx = sorted_idx

                # discharge with maximum power
                p = -vehicle.battery.unloading_curve.max_power
                # limit to GC discharge power
                # derivation and reasoning:
                # max unload power is symmetric to max load
                # timestep.power is available power without exceeding GC power
                # therefore, currently allocated power cur_power = max_power - timestep.power
                # V2G can compensate allocated power and additionally discharge to -max_power:
                # v2g_power = cur_power + max_power = max_power - timestep.power + max_power
                # sign change (because energy flows out):
                # v2g_power = timestep.power - 2*max_power

                gc_cur_discharge_power_limit = (timesteps[v2g_ts_idx]["power"]
                                                - 2*timesteps[v2g_ts_idx]["max_power"])
                cs_cur_discharge_power_limit = -(cs.max_power + cs.current_power)
                p = min(max(gc_cur_discharge_power_limit, cs_cur_discharge_power_limit, p), 0)
                power[v2g_ts_idx] = p

                if v2g_ts_idx == 0:
                    # take note of power if current timestep
                    # don't immediately apply power, as it is uncertain if is valid
                    sim_power = p

                # simulate next timesteps
                sim_vehicle.battery.soc = vehicle.battery.soc
                for cur_idx, cur_power in enumerate(power):
                    if cur_power > 0:
                        # charge (even above desired)
                        sim_vehicle.battery.load(self.interval, target_power=cur_power)
                    elif cur_power < 0:
                        # discharge
                        sim_vehicle.battery.unload(
                            self.interval, max_power=-cur_power, target_soc=self.DISCHARGE_LIMIT)

                # try to charge enough to offset V2G
                # check all timesteps with price below that of V2G TS
                # one more as break condition is at beginning of loop
                charging_ts = sorted_ts[sorted_idx:(v2g_sorted_idx + 1)]
                for (cost, ts_idx) in charging_ts:

                    if sim_vehicle.get_delta_soc() <= 0:
                        # safe: vehicle charged enough to offset V2G discharge
                        break

                    if v2g_cost <= cost:
                        # same (or lower?) cost for discharging: don't charge
                        continue

                    # charge with full power (may already have power set)
                    p = timesteps[ts_idx]["power"] - power[ts_idx]
                    p = util.clamp_power(p, vehicle, cs)
                    power[ts_idx] += p
                    sorted_idx += 1

                    if ts_idx == 0:
                        # current timestep: make note of charge (can be charged for real later)
                        sim_power = p

                    # simulate next timesteps
                    sim_vehicle.battery.soc = vehicle.battery.soc
                    for cur_idx, cur_power in enumerate(power):
                        if cur_power > 0:
                            # charge (even above desired)
                            sim_vehicle.battery.load(self.interval, target_power=cur_power)
                        elif cur_power < 0:
                            # discharge
                            sim_vehicle.battery.unload(
                                self.interval, max_power=-cur_power, target_soc=self.DISCHARGE_LIMIT
                            )
                else:
                    # loop finished without getting break from discharge compensation:
                    # vehicle could not be charged enough to offset discharge
                    # reset states
                    sim_power = None
                    power = old_power
                    sorted_idx = old_sorted_idx

                if sim_power is not None:
                    # V2G possible, current timestep has power -> apply for real
                    avg_power = 0
                    if sim_power > 0:
                        # charge
                        avg_power = vehicle.battery.load(
                            self.interval, target_power=sim_power)['avg_power']
                    elif sim_power < 0:
                        # discharge
                        info = vehicle.battery.unload(
                            self.interval, max_power=-sim_power, target_soc=self.DISCHARGE_LIMIT)
                        avg_power = -info["avg_power"]
                        discharging_stations.append(cs_id)
                    charging_stations[cs_id] = gc.add_load(cs_id, avg_power)
                    cs.current_power += avg_power
                    # current timestep will not be taken into account again
                    break
                # end apply power
            # end loop V2G

            # update timesteps info: adjust available power (simulate charging)
            sim_vehicle.battery.soc = original_soc
            for cur_idx, cur_power in enumerate(power):
                if cur_power > 0:
                    # charge (even above desired)
                    avg_power = sim_vehicle.battery.load(
                        self.interval, target_power=cur_power)["avg_power"]
                    timesteps[cur_idx]["power"] -= avg_power
                elif cur_power < 0:
                    # discharge
                    avg_power = sim_vehicle.battery.unload(
                        self.interval, max_power=-cur_power, target_soc=self.DISCHARGE_LIMIT
                    )["avg_power"]
                    timesteps[cur_idx]["power"] += avg_power

        # end loop vehicle

        # ---------- DISTRIBUTE SURPLUS ---------- #

        # all vehicles loaded
        # distribute surplus power to vehicles
        # power is clamped to CS max_power
        for vid, vehicle in vehicles:
            cs_id = vehicle.connected_charging_station
            cs = self.world_state.charging_stations[cs_id]
            avail_power = gc.get_current_load(exclude=discharging_stations)
            if avail_power < -self.EPS and cs_id not in discharging_stations:
                # vehicle is not discharging: load greedy with surplus power
                power = util.clamp_power(-avail_power, vehicle, cs)
                avg_power = vehicle.battery.load(self.interval, max_power=power)['avg_power']
                charging_stations[cs_id] = gc.add_load(cs_id, avg_power)
                cs.current_power += avg_power

        # find consecutive intervals below threshold
        for num_cheap_ts in range(len(timesteps)):
            if util.get_cost(1, timesteps[num_cheap_ts]["cost"]) > self.PRICE_THRESHOLD:
                break

        # charge/discharge batteries
        for bat_id, battery in self.world_state.batteries.items():
            avail_power = gc.get_current_load(exclude=discharging_stations)

            old_soc = battery.soc
            # default: use surplus from local generation to charge batteries
            bat_power = max(-avail_power, 0)
            # may be low price: distribute balanced to charge full
            # naive: charge greedy
            for i in range(num_cheap_ts):
                p = timesteps[i]["power"]  # ts[0] contains feed-in
                p = 0 if p < battery.min_charging_power else p
                battery.load(self.interval, max_power=p)
                if i == 0:
                    bat_power = p
            if battery.soc > (1 - self.EPS):
                # battery charged too much, find optimum
                min_power = 0
                max_power = gc.cur_max_power
                bat_power = 0
                while max_power - min_power > self.EPS:
                    power = (min_power + max_power) / 2
                    # reset SoC
                    battery.soc = old_soc
                    # simulate
                    for i in range(0, num_cheap_ts):
                        p = min(timesteps[i]["power"], power)
                        p = 0 if p < battery.min_charging_power else p
                        battery.load(self.interval, target_power=p)
                        if i == 0:
                            bat_power = p
                    if battery.soc > (1 - self.EPS):
                        max_power = power
                    else:
                        min_power = power

            # charge for real
            battery.soc = old_soc
            avg_power = battery.load(self.interval, target_power=bat_power)["avg_power"]
            gc.add_load(bat_id, avg_power)

            if avail_power > 0 and num_cheap_ts == 0 and battery.soc > 0:
                # no surplus, no cheap price: support GC by discharging
                bat_power = min(avail_power, gc.max_power + gc.get_current_load())
                bat_power = battery.unload(self.interval, target_power=bat_power)['avg_power']
                gc.add_load(bat_id, -bat_power)
                discharging_stations.append(bat_id)

        return {'current_time': self.current_time, 'commands': charging_stations}
