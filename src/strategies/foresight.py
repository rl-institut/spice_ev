import datetime

from src import events, util
from src.strategy import Strategy


class Foresight(Strategy):
    """
    Charging strategy that takes future available power and costs into account.
    """
    def __init__(self, constants, start_time, **kwargs):
        super().__init__(constants, start_time, **kwargs)
        self.description = "foresight"

        # prepare dictionary of predicted external load
        self.pred_ext_load = {}

        timesteps_per_day = int(datetime.timedelta(days=1) / self.interval)
        cur_time = start_time
        for _ in range(timesteps_per_day):
            for gc in self.world_state.grid_connectors.keys():
                self.pred_ext_load[str(cur_time.time())] = {
                    gc: 0 for gc in self.world_state.grid_connectors.keys()
                }
            cur_time += self.interval

    def step(self, event_list=[]):
        super().step(event_list)

        # update predicted external load
        timestamp = str(self.current_time.time())
        predicted_loads = self.pred_ext_load[timestamp]
        gc_info = {}
        for gc_id, gc in self.world_state.grid_connectors.items():
            predicted_load = predicted_loads[gc_id]
            actual_load = sum(gc.current_loads.values())
            predicted_loads[gc_id] = 0.25 * predicted_load + 0.75 * actual_load
            gc_info[gc_id] = [gc.cur_max_power, gc.cost]
        self.pred_ext_load[timestamp] = predicted_loads

        # reset charging station power
        for cs in self.world_state.charging_stations.values():
            cs.current_power = 0

        # gather current state of vehicles
        vehicles = {
            v_id: {
                "delta_energy": v.get_delta_soc() / 100 * v.battery.capacity,
                "timesteps": 0
            } for v_id, v in self.world_state.vehicles.items()
        }

        # gather charging vehicles, external load and prices until all vehicles gone (24h max)
        future = {}
        event_idx = 0
        timesteps_per_day = int(datetime.timedelta(days=1) / self.interval)
        timesteps_per_hour = datetime.timedelta(hours=1) / self.interval

        cur_time = self.current_time - self.interval
        for _ in range(timesteps_per_day):
            cur_time += self.interval
            dts = str(cur_time)

            # get charging vehicles
            cur_vehicles = {gc: [] for gc in self.world_state.grid_connectors.keys()}
            for v_id, vehicle in self.world_state.vehicles.items():
                needs_charging = vehicle.battery.soc < vehicle.desired_soc
                still_present = (
                    vehicle.estimated_time_of_departure > cur_time and
                    vehicle.connected_charging_station is not None)
                if still_present and needs_charging:
                    cs = self.world_state.charging_stations[vehicle.connected_charging_station]
                    cur_vehicles[cs.parent].append(v_id)
                    vehicles[v_id]["timesteps"] += 1

            if sum([len(a) for a in cur_vehicles.values()]):
                future[dts] = {gc: {
                    "vehicles": v
                } for gc, v in cur_vehicles.items()}
            else:
                # no vehicles to charge
                break

            # peek into future events for external load or cost changes
            # for event in self.world_state.future_events:
            while True:
                try:
                    event = self.world_state.future_events[event_idx]
                except IndexError:
                    break
                if event.start_time > cur_time:
                    break
                event_idx += 1
                if type(event) == events.ExternalLoad:
                    # update predicted external load
                    # update all future external loads as well?
                    # gc_id = event.grid_connector_id
                    # self.pred_ext_load[str(cur_time.time())][gc_id] = event.value
                    # TODO: find out if external load is only updated or new one
                    pass
                elif type(event) == events.GridOperatorSignal:
                    gc_id = event.grid_connector_id
                    max_power = event.max_power or gc_info[gc_id][0]
                    gc_info[gc_id][0] = min(gc_info[gc_id][0], max_power)
                    gc_info[gc_id][1] = event.cost
                elif type(event) == events.VehicleEvent:
                    # ignored: use current estimated arrival/departure times
                    pass

            # predicted external load
            cur_ext_load = self.pred_ext_load[str(cur_time.time())]

            # compute available power and associated costs
            for gc_id, gc in self.world_state.grid_connectors.items():
                available_power = gc_info[gc_id][0] - cur_ext_load[gc_id]
                # cost = util.get_cost(gc_info[gc_id][0], gc_info[gc_id][1])
                cost = util.get_cost(gc_info[gc_id][0], gc_info[gc_id][1])
                future[dts][gc_id]["power"] = available_power
                future[dts][gc_id]["costs"] = cost

        if len(future) == 0:
            # no charging
            socs = {vid: v.battery.soc for vid, v in self.world_state.vehicles.items()}
            return {'current_time': self.current_time, 'commands': {}, 'socs': socs}

        charging_stations = {}
        current_state = future[str(self.current_time)]
        # assign charging power by grid connector
        for gc_id, gc in self.world_state.grid_connectors.items():
            vehicles_present = current_state[gc_id]["vehicles"]
            available_power = current_state[gc_id]["power"]
            costs = [f[gc_id]["costs"] for f in future.values()]

            # sort charging vehicles by remaining time
            vehicles_present = sorted(vehicles_present, key=lambda vid: vehicles[vid]["timesteps"])

            for v_id in vehicles_present:
                vehicle = self.world_state.vehicles[v_id]
                delta_energy = vehicles[v_id]["delta_energy"]
                timesteps = vehicles[v_id]["timesteps"]
                cs_id = vehicle.connected_charging_station
                cs = self.world_state.charging_stations[cs_id]
                mean_power = (delta_energy / timesteps) * timesteps_per_hour

                # get normed costs in remaining timesteps
                norm_costs = [c for c in costs[:timesteps]]
                min_costs = min(norm_costs)
                max_costs = max(norm_costs)
                for i in range(len(norm_costs)):
                    if min_costs == max_costs:
                        norm_costs[i] = 1
                    else:
                        norm_costs[i] = (norm_costs[i] - min_costs) / (max_costs - min_costs)
                sum_costs = sum(norm_costs)
                avg_costs = sum_costs / len(norm_costs)
                delta_costs = avg_costs - norm_costs[0]
                factor = 1 - delta_costs

                power = mean_power * factor
                power = min(available_power, power)
                power = min(cs.max_power - cs.current_power, power)
                avg_power = vehicle.battery.load(self.interval, power)['avg_power']
                available_power -= avg_power
                charging_stations[cs_id] = gc.add_load(cs_id, avg_power)
                cs.current_power += avg_power

        socs = {vid: v.battery.soc for vid, v in self.world_state.vehicles.items()}
        return {'current_time': self.current_time, 'commands': charging_stations, 'socs': socs}
