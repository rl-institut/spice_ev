from copy import deepcopy
import datetime

from src import events, util
from src.strategy import Strategy


class FlexWindow(Strategy):
    """
    Charges balanced during given time windows
    """
    def __init__(self, constants, start_time, **kwargs):
        self.CONCURRENCY = 1.0
        self.PRICE_THRESHOLD = 0.001  # EUR/kWh
        self.HORIZON = 24  # hours ahead
        self.DISCHARGE_LIMIT = 0  # V2G: maximum depth of discharge [0-1]
        self.LOAD_STRAT = 'balanced'  # greedy, needy, balanced

        super().__init__(constants, start_time, **kwargs)
        assert len(self.world_state.grid_connectors) == 1, "Only one grid connector supported"
        self.description = "Flex Window ({}, {} hour horizon)".format(
            self.LOAD_STRAT, self.HORIZON)

        if self.LOAD_STRAT == "greedy":
            # charge vehicles in need first, then by order of departure
            self.sort_key = lambda v: (
                v.battery.soc >= v.desired_soc,
                v.estimated_time_of_departure)
        elif self.LOAD_STRAT == "needy":
            # charge cars with not much power needed first, may leave more for others
            self.sort_key = lambda v: v.get_delta_soc() * v.battery.capacity
        elif self.LOAD_STRAT == "balanced":
            # default, simple strategy: charge vehicles balanced during windows
            self.sort_key = lambda v: (
                v.battery.soc < v.desired_soc,
                v.estimated_time_of_departure)
        else:
            "Unknown charging strategy: {}".format(self.LOAD_STRAT)

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

        # reset charging station power (nothing charged yet in this timestep)
        for cs in self.world_state.charging_stations.values():
            cs.current_power = 0

        # order vehicles
        vehicles = sorted(
            [v for v in self.world_state.vehicles.values()
                if v.connected_charging_station is not None], key=self.sort_key)

        cur_window = gc.window
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
                    cur_window = event.window or cur_window
                elif type(event) == events.EnergyFeedIn:
                    cur_feed_in[event.name] = event.value
                # vehicle events ignored (use vehicle info such as estimated_time_of_departure)

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
                "window": cur_window,
            })

        # total_energy_needed = sum([v.get_energy_needed(full=True) for v in vehicles])

        for vehicle in vehicles:
            cs_id = vehicle.connected_charging_station
            cs = self.world_state.charging_stations[cs_id]

            if util.get_cost(1, gc.cost) <= self.PRICE_THRESHOLD:
                # charge max
                p = gc.cur_max_power - gc.get_current_load()
                p = util.clamp_power(p, vehicle, cs)
                avg_power = vehicle.battery.load(self.interval, p)['avg_power']
                charging_stations[cs_id] = gc.add_load(cs_id, avg_power)
                cs.current_power += avg_power
                # no further computation for this vehicle in this timestep
                continue

            sim_vehicle = deepcopy(vehicle)
            if self.LOAD_STRAT == 'balanced':
                # simple case: charge balanced during windows
                # try to charge with full power
                cur_time = self.current_time - self.interval
                for ts_info in timesteps:
                    cur_time += self.interval
                    if cur_time >= sim_vehicle.estimated_time_of_departure:
                        break
                    if ts_info["window"]:
                        p = ts_info["power"]
                        p = util.clamp_power(p, sim_vehicle, cs)
                        sim_vehicle.battery.load(self.interval, p)

                charged_in_window = sim_vehicle.get_delta_soc() <= 0

                if charged_in_window:
                    # reset sim SoC
                    sim_vehicle.battery.soc = vehicle.battery.soc

                min_power = 0
                max_power = util.clamp_power(cs.max_power, sim_vehicle, cs)
                old_soc = sim_vehicle.battery.soc
                safe = False
                power_vec = [0]*len(timesteps)
                while (charged_in_window and not safe) or max_power - min_power > self.EPS:
                    power = (min_power + max_power) / 2
                    sim_vehicle.battery.soc = old_soc

                    cur_time = self.current_time - self.interval
                    for ts_idx, ts_info in enumerate(timesteps):
                        cur_time += self.interval
                        avg_power = 0
                        if cur_time >= sim_vehicle.estimated_time_of_departure:
                            break
                        if ts_info["window"] == charged_in_window:
                            p = util.clamp_power(power, sim_vehicle, cs)
                            avg_power = sim_vehicle.battery.load(self.interval, p)["avg_power"]
                        elif not charged_in_window and ts_info["window"]:
                            # charging windows not sufficient, charge max during window
                            p = util.clamp_power(ts_info["power"], sim_vehicle, cs)
                            avg_power = sim_vehicle.battery.load(self.interval, p)["avg_power"]

                        power_vec[ts_idx] = avg_power
                        safe = sim_vehicle.get_delta_soc() <= 0
                        if safe:
                            power_vec[ts_idx+1:] = [0]*(len(timesteps) - ts_idx - 1)
                            break

                    if safe:
                        max_power = power
                    else:
                        min_power = power

                # apply power
                if gc.window:
                    p = power if charged_in_window else gc.cur_max_power - gc.get_current_load()
                else:
                    p = 0 if charged_in_window else power
                p = util.clamp_power(p, vehicle, cs)
                avg_power = vehicle.battery.load(self.interval, p)['avg_power']
                charging_stations[cs_id] = gc.add_load(cs_id, avg_power)
                cs.current_power += avg_power

                for ts_idx, ts_info in enumerate(timesteps):
                    ts_info["power"] -= power_vec[ts_idx]

        """
        # stationary batteries
        for bid, battery in self.world_state.batteries.items():
            if self.LOAD_STRAT == 'balanced':
                # find minimum power to charge battery during windows
        """

        return {'current_time': self.current_time, 'commands': charging_stations}
