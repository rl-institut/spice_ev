from copy import deepcopy
import datetime

from src import events, util
from src.strategy import Strategy


class GreedyMarket(Strategy):
    """
    Moves all charging events to times with low energy price
    """
    def __init__(self, constants, start_time, **kwargs):
        self.PRICE_THRESHOLD = 0.001  # EUR/kWh
        self.HORIZON = 24  # hours ahead
        self.DISCHARGE_LIMIT = 0  # V2G: maximum depth of discharge [0-100]

        super().__init__(constants, start_time, **kwargs)
        assert len(self.world_state.grid_connectors) == 1, "Only one grid connector supported"
        self.description = "greedy (market-oriented) with {} hour horizon".format(self.HORIZON)

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

    def step(self, event_list=[]):
        super().step(event_list)

        gc = list(self.world_state.grid_connectors.values())[0]

        # get power that can be drawn from battery in this timestep
        avail_bat_power = sum([
            bat.get_available_power(self.interval) for bat in self.world_state.batteries.values()])

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
        cur_feed_in = {k: -v for k, v in gc.current_loads.items() if v < 0}
        cur_max_power = gc.cur_max_power

        # ---------- GET NEXT EVENTS ---------- #
        timesteps = []
        vehicle_events = {vid: [] for vid in self.world_state.vehicles.keys()}

        # look ahead (limited by horizon)
        # get future events and predict external load and cost for each timestep
        # take note of vehicle arriving and leaving and their soc
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
                if type(event) == events.GridOperatorSignal:
                    # update GC info
                    cur_max_power = event.max_power or gc.max_power
                    cur_cost = event.cost or gc.cost
                elif type(event) == events.EnergyFeedIn:
                    cur_feed_in[event.name] = event.value
                elif type(event) == events.VehicleEvent:
                    vid = event.vehicle_id
                    if event.event_type == "departure":
                        vehicle_events[vid].append({
                            "ts": timestep_idx,
                            "type": "departure"
                        })
                    elif event.event_type == "arrival":
                        standing_time = event.update["estimated_time_of_departure"] - cur_time
                        arrival_event = {
                            "ts": timestep_idx,
                            "type": "arrival",
                        }
                        arrival_event.update(event.update)
                        arrival_event["standing"] = standing_time / self.interval
                        vehicle_events[vid].append(arrival_event)

            # compute available power and associated costs
            # get (predicted) external load
            if timestep_idx == 0:
                # use actual external load
                ext_load = gc.get_current_load()
                # add battery power (sign switch, as ext_load is subtracted)
                ext_load -= avail_bat_power
            else:
                ext_load = gc.get_avg_ext_load(cur_time, self.interval) - sum(cur_feed_in.values())
            timesteps.append({
                "power": cur_max_power - ext_load,
                "cost": cur_cost,
            })

        # order timesteps by cost for 1 kWh
        sorted_ts = sorted((util.get_cost(1, e["cost"]), idx) for idx, e in enumerate(timesteps))

        # ---------- ITERATE OVER VEHICLES ---------- #

        for vid, vehicle in vehicles:
            cs_id = vehicle.connected_charging_station
            cs = self.world_state.charging_stations[cs_id]

            cost_for_one = util.get_cost(1, gc.cost)
            if cost_for_one <= self.PRICE_THRESHOLD:
                # charge max
                p = gc.cur_max_power - gc.get_current_load()
                p = util.clamp_power(p, vehicle, cs)
                avg_power = vehicle.battery.load(self.interval, p)['avg_power']
                charging_stations[cs_id] = gc.add_load(cs_id, avg_power)
                cs.current_power += avg_power
                # no further computation for this vehicle in this timestep
                continue

            # price above threshold: charge at times with low cost
            # take note of achieved soc, desired_soc for each standing period
            charged_vec = [vehicle.battery.soc >= vehicle.desired_soc - self.EPS]
            # generate profile for each timestep
            vehicle_info = [{
                # fixed / init at new arrival event
                "cs_id": vehicle.connected_charging_station,
                "stand_idx": 0,
                "soc_delta": 0,
                "desired_soc": vehicle.desired_soc,
                # changed during simulation
                "power": 0,
                "soc": vehicle.battery.soc,
            }]
            # keep track of timestamp of last arrival (how long for last charge within horizon?)
            last_arrival_idx = 0
            cur_events = vehicle_events[vid]
            # how long is whole current charge duration?
            standing = (vehicle.estimated_time_of_departure - cur_time)/self.interval
            # simulate battery
            sim_battery = deepcopy(vehicle.battery)
            for ts_idx in range(1, timesteps_ahead):
                # use last info as template
                cur_info = deepcopy(vehicle_info[-1])
                # only discharged once at arrival
                cur_info["soc_delta"] = 0
                if len(cur_events) > 0 and cur_events[0]["ts"] == ts_idx:
                    # process current event, remove from queue
                    cur_event = cur_events.pop(0)
                    if cur_event["type"] == "departure":
                        cur_info["cs_id"] = None
                    else:
                        # arrival: new standing period
                        cur_info.update({
                            "stand_idx": cur_info["stand_idx"] + 1,
                            "cs_id": cur_event["connected_charging_station"],
                            "soc_delta": cur_event["soc_delta"],
                            "desired_soc": cur_event["desired_soc"],
                            "soc": cur_info["soc"] - cur_event["soc_delta"],
                        })
                        last_arrival_idx = ts_idx
                        standing = cur_event["standing"]
                        charged_vec.append(cur_info["soc"] >= cur_info["desired_soc"] - self.EPS)
                vehicle_info.append(cur_info)
            # compute last fraction, depending on how much time left
            stand_horizon = timesteps_ahead - last_arrival_idx
            if standing <= stand_horizon:
                standing = stand_horizon
            for idx in range(last_arrival_idx, timesteps_ahead):
                vehicle_info[idx]["desired_soc"] *= stand_horizon / standing
            charged_vec[-1] = vehicle_info[-1]["soc"] >= vehicle_info[-1]["desired_soc"] - self.EPS

            # ---------- SIMULATE CHARGE ---------- #

            need_charging = sum([1 - b for b in charged_vec])
            power_vec = [0]*len(timesteps)

            # iterate timesteps by order of cheapest price
            sorted_idx = -1
            for cost, ts_idx in sorted_ts:
                if need_charging == 0:
                    # all standing times satisfied -> no need to charge
                    continue

                sorted_idx += 1

                power = 0
                ts_info = timesteps[ts_idx]
                cv_info = vehicle_info[ts_idx]
                cs_id = cv_info["cs_id"]
                if cs_id is None:
                    # vehicle not present
                    continue
                cs = self.world_state.charging_stations[cs_id]
                # how many future standing times are not at desired SoC yet?
                needy = sum([1 - b for b in charged_vec[(cv_info["stand_idx"] + 1):]])

                if cost <= self.PRICE_THRESHOLD or needy > 0:
                    # cheap energy price
                    # OR there are future standing times that are not at desired SoC
                    # -> charge with full power (cheapest possible price)
                    power = ts_info["power"]
                    power = util.clamp_power(power, vehicle, cs)
                elif not charged_vec[cv_info["stand_idx"]]:
                    # future standing times satisified, but not current
                    # charge not with full power, but minimum required power
                    sim_battery.soc = cv_info["soc"]
                    desired_soc = cv_info["desired_soc"]
                    power = ts_info["power"]
                    power = util.clamp_power(power, vehicle, cs)
                    sim_battery.load(self.interval, power, desired_soc)
                    if sim_battery.soc >= desired_soc - self.EPS:
                        # charged too much -> try to find optimum
                        max_power = power
                        min_power = max(0, vehicle.vehicle_type.min_charging_power, cs.min_power)
                        while max_power - min_power > self.EPS:
                            # reset simulated SoC
                            sim_battery.soc = cv_info["soc"]
                            # binary search
                            power = (max_power + min_power) / 2
                            sim_battery.load(self.interval, power, desired_soc)
                            if sim_battery.soc < desired_soc:
                                # not enough power
                                min_power = power
                            else:
                                # too much power
                                max_power = power

                if power > 0:
                    # simulate from this timestep on -> update future SoC and charged_vec
                    cv_info["power"] = power
                    # reset battery to initial SoC (ignore soc_delta)
                    sim_battery.soc = cv_info["soc"] + cv_info["soc_delta"]
                    for cur_idx, cur_info in enumerate(vehicle_info[ts_idx:]):
                        sim_battery.soc -= cur_info["soc_delta"]
                        desired_soc = cur_info["desired_soc"]
                        if cur_info["power"] > 0:
                            info = sim_battery.load(self.interval, cur_info["power"], desired_soc)
                            power_vec[ts_idx + cur_idx] = info["avg_power"]
                        cur_info["soc"] = sim_battery.soc
                        is_charged = sim_battery.soc >= desired_soc - self.EPS
                        charged_vec[cur_info["stand_idx"]] = is_charged

                    need_charging = sum([1 - b for b in charged_vec])

                if ts_idx == 0 and power > 0:
                    # current timestep: charge for real
                    avg_power = vehicle.battery.load(
                        self.interval, power)['avg_power']
                    charging_stations[cs_id] = gc.add_load(cs_id, avg_power)
                    cs.current_power += avg_power

            # normal charging done

            if not vehicle.vehicle_type.v2g:
                # rest of loop is for V2G only
                # adjust available power
                for ts_idx, ts_info in enumerate(timesteps):
                    ts_info["power"] += power_vec[ts_idx]
                continue

            # ---------- VEHICLE TO GRID ---------- #

            # begin vehicle-to-grid/home at time with highest price
            # and stop once it reaches charging timestep
            # off-by-one: v2g_sorted_idx is immediately decreased by one
            v2g_sorted_idx = len(sorted_ts)
            while v2g_sorted_idx > (sorted_idx + 1):
                sim_power = None
                v2g_sorted_idx -= 1
                v2g_cost, v2g_ts_idx = sorted_ts[v2g_sorted_idx]
                if v2g_cost < self.PRICE_THRESHOLD:
                    # too cheap for discharging energy: don't
                    break

                cv_info = vehicle_info[v2g_ts_idx]
                cs_id = cv_info["cs_id"]
                if cv_info["power"] != 0:
                    # already action at this TS
                    continue

                if cs_id is None:
                    # not present
                    continue

                # save current solution for backtracing
                old_charged_vec = deepcopy(charged_vec)
                old_vehicle_info = deepcopy(vehicle_info)
                old_timestep_info = deepcopy(timesteps)

                # use maximum possible power when discharging
                # assumption: can discharge with max loading curve power, regardless of SoC
                power = vehicle.battery.loading_curve.max_power
                # set battery SoC (soc_delta subtracted later)
                sim_battery.soc = cv_info["soc"] + cv_info["soc_delta"]
                cv_info["power"] = -power
                # make power available globally
                timesteps[v2g_ts_idx]["power"] += power

                if v2g_ts_idx == 0:
                    # take note of power if current timestep
                    # don't immediatley apply power, as it is uncertain if is valid
                    sim_power = -power

                # simulate next timesteps
                for cur_idx, cur_info in enumerate(vehicle_info[v2g_ts_idx:]):
                    sim_battery.soc -= cur_info["soc_delta"]
                    desired_soc = cur_info["desired_soc"]
                    if cur_info["power"] > 0:
                        # charge (even above desired)
                        avg_power = sim_battery.load(self.interval, cur_info["power"])["avg_power"]
                        power_vec[v2g_ts_idx + cur_idx] = avg_power
                    if cur_info["power"] < 0:
                        # discharge / no action
                        power = -cur_info["power"]
                        limit = self.DISCHARGE_LIMIT
                        avg_power = sim_battery.unload(self.interval, power, limit)["avg_power"]
                        power_vec[v2g_ts_idx + cur_idx] = avg_power

                    cur_info["soc"] = sim_battery.soc
                    charged_vec[cur_info["stand_idx"]] = sim_battery.soc >= desired_soc
                # end update next TS

                old_sorted_idx = sorted_idx
                need_charging = sum([1 - b for b in charged_vec])

                # try to charge enough to offset V2G
                # check all timesteps with price below that of V2G TS
                # one more as break condition is at beginning of loop
                for (cost, ts_idx) in sorted_ts[:(v2g_sorted_idx + 1)]:

                    if need_charging == 0:
                        break

                    if v2g_cost <= cost:
                        # same (or lower?) cost for discharging: don't charge
                        continue

                    # vehicle info at this TS
                    cv_info = vehicle_info[ts_idx]
                    # number of past trips with insufficient charge
                    need_charging_past = sum([1 - b for b in charged_vec[:cv_info["stand_idx"]]])
                    # number of future trips with insufficient charge
                    need_charging_future = sum([1 - b for b in charged_vec[cv_info["stand_idx"]:]])
                    if need_charging_past + need_charging_future == 0:
                        # discharge can be compensated: stop charging (changes keep applied)
                        break

                    if need_charging_future == 0:
                        # charging now does not help
                        continue

                    if cv_info["power"] != 0:
                        # power previously applied
                        continue

                    cs_id = cv_info["cs_id"]
                    if cs_id is None:
                        # vehicle not present
                        continue

                    # TS in present or future with insufficient charge
                    # charge with full power
                    cs = self.world_state.charging_stations[cs_id]
                    power = timesteps[ts_idx]["power"]
                    power = util.clamp_power(power, vehicle, cs)
                    # allocate power
                    cv_info["power"] = power
                    timesteps[ts_idx]["power"] -= power

                    # reset battery to initial SoC (ignore soc_delta)
                    sim_battery.soc = cv_info["soc"] + cv_info["soc_delta"]
                    # simulate from this timestep, update future SoC and charged_vec
                    for cur_idx, cur_info in enumerate(vehicle_info[ts_idx:]):
                        sim_battery.soc -= cur_info["soc_delta"]
                        desired_soc = cur_info["desired_soc"]
                        if cur_info["power"] > 0:
                            # charge
                            info = sim_battery.load(self.interval, cur_info["power"])
                            power_vec[ts_idx + cur_idx] = info["avg_power"]
                        if cur_info["power"] < 0:
                            # discharge
                            power = -cur_info["power"]
                            limit = self.DISCHARGE_LIMIT
                            info = sim_battery.unload(self.interval, power, self.DISCHARGE_LIMIT)
                            power_vec[ts_idx + cur_idx] = info["avg_power"]
                        cur_info["soc"] = sim_battery.soc
                        is_charged = sim_battery.soc >= desired_soc - self.EPS
                        charged_vec[cur_info["stand_idx"]] = is_charged

                    if ts_idx == 0:
                        # current timestep: make note of charge (can be charged for real later)
                        sim_power = power
                else:
                    # loop finished without getting break from discharge compensation:
                    # vehicle could not be charged enough to offset discharge
                    # reset states
                    sim_power = None
                    timesteps = old_timestep_info
                    sorted_idx = old_sorted_idx
                    charged_vec = old_charged_vec
                    vehicle_info = old_vehicle_info

                if sim_power is not None:
                    # V2G possible, current timestep has power -> apply for real
                    if sim_power > 0:
                        # charge
                        avg_power = vehicle.battery.load(self.interval, sim_power)['avg_power']
                        charging_stations[cs_id] = gc.add_load(cs_id, avg_power)
                        cs.current_power += avg_power
                    else:
                        # discharge
                        avg_power = vehicle.battery.unload(
                            self.interval, -sim_power, self.DISCHARGE_LIMIT)["avg_power"]
                        charging_stations[cs_id] = gc.add_load(cs_id, -avg_power)
                        cs.current_power -= avg_power
                        discharging_stations.append(cs_id)
                # end apply power
            # end loop V2G

            # update timesteps info: adjust available power
            for ts_idx, ts_info in enumerate(timesteps):
                ts_info["power"] += power_vec[ts_idx]

            # try next lowest price
        # end loop vehicle

        # ---------- DISTRIBUTE SURPLUS ---------- #

        # all vehicles loaded
        # distribute surplus power to vehicles
        # power is clamped to CS max_power
        for vid, vehicle in vehicles:
            cs_id = vehicle.connected_charging_station
            cs = self.world_state.charging_stations[cs_id]
            avail_power = gc.get_current_load(exclude=discharging_stations)
            if avail_power < 0:
                # surplus power
                power = util.clamp_power(-avail_power, vehicle, cs)
                avg_power = vehicle.battery.load(self.interval, power)['avg_power']
                charging_stations[cs_id] = gc.add_load(cs_id, avg_power)
                cs.current_power += avg_power

        # charge/discharge batteries
        for bat_id, battery in self.world_state.batteries.items():
            avail_power = gc.get_current_load(exclude=discharging_stations)
            if util.get_cost(1, gc.cost) <= self.PRICE_THRESHOLD:
                # low price: charge with full power
                power = gc.cur_max_power - avail_power
                power = 0 if power < battery.min_charging_power else power
                avg_power = battery.load(self.interval, power)['avg_power']
                gc.add_load(bat_id, avg_power)
            elif avail_power < 0:
                # surplus energy: charge
                power = -avail_power
                power = 0 if power < battery.min_charging_power else power
                avg_power = battery.load(self.interval, power)['avg_power']
                gc.add_load(bat_id, avg_power)
            else:
                # GC draws power: use stored energy to support GC
                bat_power = battery.unload(self.interval, avail_power)['avg_power']
                gc.add_load(bat_id, -bat_power)
                discharging_stations.append(bat_id)

        return {'current_time': self.current_time, 'commands': charging_stations}
