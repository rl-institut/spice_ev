from copy import deepcopy
import datetime

from src import events, util
from src.strategy import Strategy


class BalancedMarket(Strategy):
    """
    Moves all charging events to times with low energy price
    """
    def __init__(self, constants, start_time, **kwargs):
        self.CONCURRENCY = 1.0
        self.PRICE_THRESHOLD = 0.001  # EUR/kWh
        self.HORIZON = 24  # maximum number of hours ahead
        self.DISCHARGE_LIMIT = 0  # V2G: maximum depth of discharge [0-100]

        super().__init__(constants, start_time, **kwargs)
        assert len(self.world_state.grid_connectors) == 1, "Only one grid connector supported"
        self.description = "balanced (market-oriented)"

        # concurrency: set fraction of maximum available power at each charging station
        for cs in self.world_state.charging_stations.values():
            cs.max_power = self.CONCURRENCY * cs.max_power

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

        # look ahead (limited by horizon)
        # get future events and predict external load and cost for each timestep
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
                    cur_max_power = event.max_power or cur_max_power
                    cur_cost = event.cost or cur_cost
                elif type(event) == events.EnergyFeedIn:
                    cur_feed_in[event.name] = event.value
                # vehicle events ignored (use vehicle info such as estimated_time_of_departure)

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

        # ---------- ITERATE OVER VEHICLES ---------- #

        for vid, vehicle in vehicles:
            cs_id = vehicle.connected_charging_station
            cs = self.world_state.charging_stations[cs_id]

            # special case cheap energy: charge full (greedy)
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

            # iterate timesteps by order of cheapest price to reach desired soc
            sorted_idx = 0
            while sorted_idx < len(sorted_ts) and sim_vehicle.get_delta_soc() > self.EPS:
                cost, start_idx = sorted_ts[sorted_idx]

                # find timesteps with same price
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

                # naive: charge with full power during all timesteps
                old_soc = sim_vehicle.battery.soc
                for ts_idx in same_price_ts:
                    p = timesteps[ts_idx]["power"]
                    p = util.clamp_power(p, vehicle, cs)
                    power[ts_idx] = p
                    sim_vehicle.battery.load(self.interval, p)
                if sim_vehicle.get_delta_soc() < -self.EPS:
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
                            sim_vehicle.battery.load(self.interval, p)
                        safe = sim_vehicle.get_delta_soc() < -self.EPS
                        if not safe:
                            # not charged enough
                            min_power = cur_power
                        else:
                            max_power = cur_power

                if start_idx == 0 and power[0]:
                    # current timestep: charge vehicle for real
                    avg_power = vehicle.battery.load(self.interval, power[0])['avg_power']
                    charging_stations[cs_id] = gc.add_load(cs_id, avg_power)
                    cs.current_power += avg_power
                    # don't have to simulate further
                    break

            # normal charging done

            if not vehicle.vehicle_type.v2g:
                # rest of loop is for V2G only
                # adjust available power
                for ts_idx, p in enumerate(power):
                    timesteps[ts_idx]["power"] -= p
                continue

            # ---------- VEHICLE TO GRID ---------- #

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
