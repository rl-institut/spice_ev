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

        # reset charging station power (nothing charged yet in this timestep)
        for cs in self.world_state.charging_stations.values():
            cs.current_power = 0

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
                "ext_load": ext_load,
                "window": cur_window,
            })

        if util.get_cost(1, gc.cost) <= self.PRICE_THRESHOLD:
            # charge max
            commands = {}
            for vehicle in self.world_state.vehicles.values():
                cs_id = vehicle.connected_charging_station
                if cs_id is None:
                    continue
                cs = self.world_state.charging_stations[cs_id]
                p = gc.cur_max_power - gc.get_current_load()
                p = util.clamp_power(p, vehicle, cs)
                avg_power = vehicle.battery.load(self.interval, p)['avg_power']
                commands[cs_id] = gc.add_load(cs_id, avg_power)
                cs.current_power += avg_power
        elif self.LOAD_STRAT == 'balanced':
            commands = self.distribute_balanced(timesteps)
        else:
            commands = self.distribute_peak_shaving(timesteps)

        """
        # stationary batteries
        for bid, battery in self.world_state.batteries.items():
            if self.LOAD_STRAT == 'balanced':
                # find minimum power to charge battery during windows
        """
        return {'current_time': self.current_time, 'commands': commands}

    def distribute_balanced(self, timesteps):

        gc = list(self.world_state.grid_connectors.values())[0]
        # order vehicles
        vehicles = sorted(
            [v for v in self.world_state.vehicles.values()
                if v.connected_charging_station is not None], key=self.sort_key)

        # dict to hold charging commands
        charging_stations = {}

        for vehicle in vehicles:
            cs_id = vehicle.connected_charging_station
            cs = self.world_state.charging_stations[cs_id]
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

        return charging_stations

    def distribute_peak_shaving(self, timesteps):

        gc = list(self.world_state.grid_connectors.values())[0]
        # order vehicles
        vehicles = sorted(
            [v for v in self.world_state.vehicles.values()
                if v.connected_charging_station is not None], key=self.sort_key)

        # what happens when all cars are charged with maximum power during charging windows?
        sim_vehicles = deepcopy(vehicles)
        cur_vehicles = sim_vehicles

        cur_time = self.current_time - self.interval
        for ts_info in timesteps:
            cur_time += self.interval
            cur_vehicles = [v for v in cur_vehicles if v.estimated_time_of_departure > cur_time]
            cur_needed = sum([v.get_energy_needed(full=True) for v in cur_vehicles])
            if not cur_vehicles or cur_needed < self.EPS:
                # no cars or no energy need: skip check
                break
            self.distribute_power(cur_vehicles, ts_info["power"], cur_needed)
        charged_in_window = all([v.get_delta_soc() < self.EPS for v in sim_vehicles])

        if charged_in_window:
            # can be charged within windows: reset SoC
            for i, v in enumerate(sim_vehicles):
                v.battery.soc = vehicles[i].battery.soc

        old_soc = [v.battery.soc for v in sim_vehicles]

        min_total_power = -gc.max_power
        max_total_power = gc.max_power
        # power_vec = [0]*len(timesteps)

        safe = False

        # while (charged_in_window and not safe) or max_total_power - min_total_power > self.EPS:
        while max_total_power - min_total_power > self.EPS:
            total_power = (min_total_power + max_total_power) / 2

            # reset SoC
            cur_vehicles = sim_vehicles
            for i, v in enumerate(sim_vehicles):
                v.battery.soc = old_soc[i]

            cur_time = self.current_time - self.interval
            for ts_info in timesteps:
                cur_time += self.interval
                new_cur_vehicles = []
                for v in cur_vehicles:
                    if v.estimated_time_of_departure > cur_time:
                        new_cur_vehicles.append(v)
                    elif v.get_delta_soc() > self.EPS:
                        break
                cur_vehicles = new_cur_vehicles
                cur_needed = sum([v.get_energy_needed(full=True) for v in cur_vehicles])
                if not cur_vehicles or cur_needed < self.EPS:
                    # no cars or no energy need: skip simulation
                    break
                if ts_info["window"] == charged_in_window:
                    self.distribute_power(
                        cur_vehicles, total_power - ts_info["ext_load"], cur_needed)
                elif not charged_in_window and ts_info["window"]:
                    self.distribute_power(cur_vehicles, ts_info["power"], cur_needed)

            safe = all([v.get_delta_soc() < self.EPS for v in sim_vehicles])

            if safe:
                max_total_power = total_power
            else:
                min_total_power = total_power

        # apply power
        total_energy_needed = sum([v.get_energy_needed(full=True) for v in vehicles])

        # dict to hold charging commands
        commands = {}
        if gc.window == charged_in_window:
            commands = self.distribute_power(
                vehicles, total_power - gc.get_current_load(), total_energy_needed)
        elif not charged_in_window and gc.window:
            commands = self.distribute_power(
                vehicles, gc.max_power - gc.get_current_load(), total_energy_needed)

        for cs_id, power in commands.items():
            commands[cs_id] = gc.add_load(cs_id, power)
            # cs.current_power += power

        # print(total_power, total_energy_needed, sum(commands.values()))
        # raise Exception
        return commands

    def distribute_power(self, vehicles, total_power, total_needed):
        commands = {}
        power = 0
        if total_power <= 0 or total_needed <= 0:
            return {}

        for v in vehicles:
            cs_id = v.connected_charging_station
            cs = self.world_state.charging_stations[cs_id]
            if self.LOAD_STRAT == 'greedy':
                power = total_power
            elif self.LOAD_STRAT == 'needy':
                energy_needed = v.get_energy_needed(full=True)
                f = energy_needed / total_needed if total_needed > 0 else 0
                power = f * total_power
            else:
                raise NotImplementedError
            power = util.clamp_power(power, v, cs)
            avg_power = v.battery.load(self.interval, power)["avg_power"]
            commands[cs_id] = avg_power
        return commands
