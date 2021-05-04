from copy import deepcopy
import datetime

from netz_elog import events, util
from netz_elog.strategy import Strategy


class GreedyForesight(Strategy):
    """
    Moves all charging events to times with low energy price
    """
    def __init__(self, constants, start_time, **kwargs):
        self.CONCURRENCY=1.0
        self.description = "greedy (foresight)"
        self.PRICE_THRESHOLD = 0.1
        self.EPS= 1e-3

        super().__init__(constants, start_time, **kwargs)
        assert len(self.world_state.grid_connectors) == 1, "Only one grid connector supported"

    def step(self, event_list=[]):
        super().step(event_list)

        gc = list(self.world_state.grid_connectors.values())[0]
        vehicles = sorted([(v_id, v) for (v_id, v) in self.world_state.vehicles.items() if v.connected_charging_station is not None], key = lambda x: (x[1].battery.capacity*x[1].battery.soc/self.world_state.charging_stations[x[1].connected_charging_station].max_power) / ((x[1].estimated_time_of_departure - self.current_time) / self.interval))

        cur_max_power = gc.cur_max_power
        cur_cost = gc.cost

        timesteps = []

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
                    cur_max_power = event.max_power or gc.max_power
                    cur_cost = event.cost

            # compute available power and associated costs
            # get (predicted) external load
            if timestep_idx == 0:
                # use actual external load
                ext_load = gc.get_current_load()
            else:
                ext_load = gc.get_avg_ext_load(cur_time, self.interval)
            timesteps.append({
                "power": cur_max_power - ext_load,
                "cost": cur_cost,
            })

        if len(timesteps) == 0:
            # no timesteps -> no charging
            socs={vid: v.battery.soc for vid, v in self.world_state.vehicles.items()}
            return {'current_time': self.current_time, 'commands': {}, 'socs': socs}

        # order timesteps by cost for 1 kWh
        sorted_ts = sorted((util.get_cost(1, e["cost"]), idx) for idx, e in enumerate(timesteps))

        charging_stations = {}
        for v_id, vehicle in vehicles:
            cs_id = vehicle.connected_charging_station
            cs = self.world_state.charging_stations[cs_id]

            cost_for_one = util.get_cost(1, gc.cost)
            if cost_for_one <= self.PRICE_THRESHOLD:
                # charge max
                p = gc.cur_max_power - gc.get_current_load()
                p = util.clamp_power(p, vehicle, cs)
                avg_power = vehicle.battery.load(self.interval,p)['avg_power']
                charging_stations[cs_id] = gc.add_load(cs_id, avg_power)
            else:
                # price above threshold: find times with low cost
                sim_vehicle = deepcopy(vehicle)
                departure_index = int((vehicle.estimated_time_of_departure - self.current_time) / self.interval)
                for (cost, ts_idx) in sorted_ts:

                    cur_power = 0

                    if departure_index <= ts_idx:
                        # vehicle already left
                        continue
                    if cost <= self.PRICE_THRESHOLD:
                        # charge max
                        cur_power = timesteps[ts_idx]["power"]
                        cur_power = util.clamp_power(cur_power, sim_vehicle, cs)
                        avg_power = sim_vehicle.battery.load(self.interval,cur_power)['avg_power']
                        timesteps[ts_idx]["power"] -= avg_power
                    elif sim_vehicle.battery.soc < vehicle.desired_soc:
                        # needs charging: try to reach desired SOC
                        old_sim_soc = sim_vehicle.battery.soc
                        min_power = max(0, vehicle.vehicle_type.min_charging_power, cs.min_power)
                        max_power = timesteps[ts_idx]["power"]
                        cur_power = max_power
                        while max_power - min_power > self.EPS:
                            cur_power = (max_power + min_power) / 2
                            sim_vehicle.battery.load(self.interval, cur_power)
                            if sim_vehicle.battery.soc < vehicle.desired_soc:
                                # not enough power
                                min_power = cur_power
                            else:
                                # too much power
                                max_power = cur_power
                            sim_vehicle.battery.soc = old_sim_soc
                        # allocate charge
                        avg_power = sim_vehicle.battery.load(self.interval,cur_power)['avg_power']
                        timesteps[ts_idx]["power"] -= avg_power

                    if ts_idx == 0:
                        # current timestep: charge for real
                        avg_power = vehicle.battery.load(self.interval, cur_power)['avg_power']
                        charging_stations[cs_id] = gc.add_load(cs_id, avg_power)

        socs={vid: v.battery.soc for vid, v in self.world_state.vehicles.items()}
        return {'current_time': self.current_time, 'commands': charging_stations, 'socs': socs}
