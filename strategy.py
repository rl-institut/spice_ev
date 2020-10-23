from copy import deepcopy
import datetime

import events
import util

def class_from_str(strategy_name):
    strategy_name = strategy_name.lower()
    if   strategy_name == 'greedy':
        return Greedy
    elif strategy_name == 'parity':
        return Parity
    elif strategy_name == 'balanced':
        return Balanced
    elif strategy_name == 'foresight':
        return Foresight
    else:
        raise Exception('unknown strategy with name {}'.format(strategy_name))


class Strategy():
    """ strategy
    """

    def __init__(self, constants, start_time, interval):
        self.world_state = deepcopy(constants)
        self.world_state.future_events = []
        self.current_time = start_time - interval
        self.interval = interval

    def step(self, event_list=[]):
        self.current_time += self.interval

        self.world_state.future_events += event_list
        self.world_state.future_events.sort(key = lambda ev: ev.start_time)

        while True:
            if len(self.world_state.future_events) == 0:
                break
            elif self.world_state.future_events[0].start_time > self.current_time:
                # ignore future events
                break

            # remove event from list
            ev = self.world_state.future_events.pop(0)

            if type(ev) == events.ExternalLoad:
                connector = self.world_state.grid_connectors[ev.grid_connector_id]
                assert ev.name not in self.world_state.charging_stations, "External load must not be from charging station"
                connector.current_loads[ev.name] = ev.value # not reset after last event
            elif type(ev) == events.GridOperatorSignal:
                connector = self.world_state.grid_connectors[ev.grid_connector_id]
                if ev.cost:
                    # set power cost
                    connector.cost = ev.cost
                # set max power from event
                if connector.max_power:
                    if ev.max_power:
                        connector.cur_max_power = min(connector.max_power, ev.max_power)
                    else:
                        # event max power not set: reset to connector power
                        connector.cur_max_power = connector.max_power
                else:
                    # connector max power not set
                    connector.cur_max_power = ev.max_power

            elif type(ev) == events.VehicleEvent:
                vehicle = self.world_state.vehicles[ev.vehicle_id]
                for k,v in ev.update.items():
                    setattr(vehicle, k, v)
                if ev.event_type == "departure":
                    vehicle.connected_charging_station = None
                    assert vehicle.battery.soc >= vehicle.desired_soc * 0.95, "{}: Vehicle {} is below desired SOC ({} < {})".format(ev.start_time.isoformat(), ev.vehicle_id, vehicle.battery.soc, vehicle.desired_soc)
                elif ev.event_type == "arrival":
                    assert vehicle.connected_charging_station is not None
                    assert hasattr(vehicle, 'soc_delta')
                    vehicle.battery.soc += vehicle.soc_delta
                    assert vehicle.battery.soc >= 0, 'SOC of vehicle {} should not be negative. SOC is {}, soc_delta was {}'.format(ev.vehicle_id, vehicle.battery.soc, vehicle.soc_delta)
                    delattr(vehicle, 'soc_delta')


            else:
                raise Exception("Unknown event type: {}".format(ev))

        for name, connector in self.world_state.grid_connectors.items():
            # reset charging stations at grid connector
            for load_name in list(connector.current_loads.keys()):
                if load_name in self.world_state.charging_stations.keys():
                    # connector.current_loads[load_name] = 0
                    del connector.current_loads[load_name]

            # check for associated costs
            if not connector.cost:
                raise Exception("Warning: Connector {} has no associated costs at {}".format(name, time))


class Greedy(Strategy):
    def __init__(self, constants, start_time, interval):
        super().__init__(constants, start_time, interval)
        self.description = "greedy"


    def step(self, event_list=[]):
        super().step(event_list)

        charging_stations = {}
        socs = {}

        for vehicle_id in sorted(self.world_state.vehicles):
            vehicle = self.world_state.vehicles[vehicle_id]
            delta_soc = vehicle.desired_soc - vehicle.battery.soc
            cs_id = vehicle.connected_charging_station
            if delta_soc > 0 and cs_id:
                cs = self.world_state.charging_stations[cs_id]
                # vehicle needs loading
                gc = self.world_state.grid_connectors[cs.parent]
                gc_power_left = gc.cur_max_power - sum(gc.current_loads.values())
                cs_power_left = cs.max_power - charging_stations.get(cs_id, 0)
                max_power =  min(cs_power_left, gc_power_left)

                load_result = vehicle.battery.load(self.interval, max_power)
                avg_power = load_result['avg_power']

                charging_stations[cs_id] = gc.add_load(cs_id, avg_power)

                assert vehicle.battery.soc <= 100
                assert vehicle.battery.soc >= 0, 'SOC of {} is {}'.format(vehicle_id, vehicle.battery.soc)

            socs[vehicle_id] = vehicle.battery.soc

        return {'current_time': self.current_time, 'commands': charging_stations, 'socs': socs}

class Parity(Strategy):
    """
    Charging strategy that distributes power evenly among cars.
    """
    def __init__(self, constants, start_time, interval):
        super().__init__(constants, start_time, interval)
        self.description = "parity"


    def step(self, event_list=[]):
        super().step(event_list)

        # charging vehicle at which grid connector?
        vehicle_to_grid = {}
        charging_stations = {}

        # gather all vehicles in need of charge
        for vehicle_id, vehicle in self.world_state.vehicles.items():
            delta_soc = vehicle.desired_soc - vehicle.battery.soc
            cs_id = vehicle.connected_charging_station
            if delta_soc > 0 and cs_id:
                cs = self.world_state.charging_stations[cs_id]
                gc_id = cs.parent
                if gc_id in vehicle_to_grid:
                    vehicle_to_grid[gc_id].append(vehicle_id)
                else:
                    vehicle_to_grid[gc_id] = [vehicle_id]

        # distribute power of each grid connector
        for gc_id, gc in self.world_state.grid_connectors.items():
            gc_power_left = gc.cur_max_power - sum(gc.current_loads.values())
            vehicles = vehicle_to_grid.get(gc_id, [])

            for vehicle_id in vehicles:
                vehicle = self.world_state.vehicles[vehicle_id]
                cs_id = vehicle.connected_charging_station
                cs = self.world_state.charging_stations[cs_id]
                vehicles_at_cs = list(filter(lambda v: self.world_state.vehicles[v].connected_charging_station == cs_id, vehicles))

                # find minimum of distributed power and charging station power
                gc_dist_power = gc_power_left / len(vehicles)
                gc_dist_power = min(gc_dist_power, cs.max_power)
                # CS guaranteed to have one requesting vehicle
                cs_dist_power = gc_dist_power / len(vehicles_at_cs)

                # load battery
                load_result = vehicle.battery.load(self.interval, cs_dist_power)
                avg_power = load_result['avg_power']

                charging_stations[cs_id] = gc.add_load(cs_id, avg_power)

                assert vehicle.battery.soc <= 100
                assert vehicle.battery.soc >= 0, 'SOC of {} is {}'.format(vehicle_id, vehicle.battery.soc)

        socs={vid: v.battery.soc for vid, v in self.world_state.vehicles.items()}

        return {'current_time': self.current_time, 'commands': charging_stations, 'socs': socs}


class Balanced(Strategy):
    """
    Charging strategy that calculates the minimum charging power to arrive at the
    desired SOC during the estimated parking time for each vehicle.
    """
    def __init__(self, constants, start_time, interval):
        super().__init__(constants, start_time, interval)
        self.description = "balanced"


    def step(self, event_list=[]):
        super().step(event_list)

        charging_stations = {}
        EPS = 1e-5
        ITERATIONS = 10

        for vehicle_id in sorted(self.world_state.vehicles):
            # get vehicle
            vehicle = self.world_state.vehicles[vehicle_id]
            delta_soc = vehicle.desired_soc - vehicle.battery.soc
            cs_id = vehicle.connected_charging_station
            if cs_id is None:
                # not connected
                continue
            # get connected charging station
            cs = self.world_state.charging_stations[cs_id]

            if delta_soc > EPS:
                # vehicle needs charging
                if cs.current_power == 0:
                    # not precomputed
                    min_power = vehicle.vehicle_type.min_charging_power
                    max_power = vehicle.vehicle_type.charging_curve.max_power
                    # time until departure
                    dt = vehicle.estimated_time_of_departure - self.current_time - datetime.timedelta(hours=1)
                    old_soc = vehicle.battery.soc
                    idx = 0
                    safe = False
                    # converge to optimal power for the duration
                    # at least ITERATIONS cycles
                    # must end with slightly too much power used
                    # abort if min_power == max_power (e.g. unrealistic goal)
                    while (idx < ITERATIONS or not safe) and max_power - min_power > EPS:
                        idx += 1
                        # get new power value
                        power = (max_power + min_power) / 2
                        # load whole time with same power
                        charged_soc = vehicle.battery.load(dt, power)["soc_delta"]
                        # reset SOC
                        vehicle.battery.soc = old_soc

                        if delta_soc - charged_soc > EPS: #charged_soc < delta_soc
                            # power not enough
                            safe = False
                            min_power = power
                        elif charged_soc - delta_soc > EPS: #charged_soc > delta_soc:
                            # power too much
                            safe = True
                            max_power = power
                        else:
                            # power exactly right
                            break

                    # add safety margin
                    # power *= 1.1
                    cs.current_power = power
                else:
                    # power precomputed: use again
                    power = cs.current_power

                gc = self.world_state.grid_connectors[cs.parent]
                gc_power_left = max(0, gc.cur_max_power - sum(gc.current_loads.values()))
                old_soc = vehicle.battery.soc
                # load with power
                avg_power = vehicle.battery.load(self.interval, power)['avg_power']
                if avg_power > gc_power_left:
                    # GC at limit: try again with less power
                    vehicle.battery.soc = old_soc
                    avg_power = vehicle.battery.load(self.interval, gc_power_left)['avg_power']
                    # compute new plan next time
                    cs.current_power = 0

                assert vehicle.battery.soc <= 100
                assert vehicle.battery.soc >= 0, 'SOC of {} is {}'.format(vehicle_id, vehicle.battery.soc)

                charging_stations[cs_id] = gc.add_load(cs_id, avg_power)

        # update charging stations
        for cs_id, cs in self.world_state.charging_stations.items():
            if cs_id not in charging_stations:
                # CS currently inactive
                cs.current_power = 0
            else:
                # can active charging station bear minimum load?
                assert cs.max_power >= cs.current_power - EPS, "{} - {} over maximum load ({} > {})".format(self.current_time, cs_id, cs.current_power, cs.max_power)
                # can grid connector bear load?
                gc = self.world_state.grid_connectors[cs.parent]
                gc_current_power = sum(gc.current_loads.values())
                assert  gc.cur_max_power >= gc_current_power - EPS, "{} - {} over maximum load ({} > {})".format(self.current_time, cs.parent, gc_current_power, gc.cur_max_power)

        socs={vid: v.battery.soc for vid, v in self.world_state.vehicles.items()}

        return {'current_time': self.current_time, 'commands': charging_stations, 'socs': socs}

class Foresight(Strategy):
    """
    Charging strategy that takes future available power and costs into account.
    """
    def __init__(self, constants, start_time, interval):
        super().__init__(constants, start_time, interval)
        self.description = "foresight"

        # prepare dictionary of predicted external load
        self.pred_ext_load = {}

        timesteps_per_day = int(datetime.timedelta(days=1) / interval)
        cur_time = start_time
        for _ in range(timesteps_per_day):
            for gc in self.world_state.grid_connectors.keys():
                self.pred_ext_load[str(cur_time.time())] = {
                    gc: 0 for gc in self.world_state.grid_connectors.keys()
                }
            cur_time += interval

    def step(self, event_list=[]):
        super().step(event_list)

        # update predicted external load
        timestamp = str(self.current_time.time())
        predicted_loads = self.pred_ext_load[timestamp]
        gc_info = {}
        for gc_id, gc in self.world_state.grid_connectors.items():
            predicted_load = predicted_loads[gc_id]
            actual_load = sum(gc.current_loads.values())
            predicted_loads[gc_id] = 0.3 * predicted_load + 0.7 * actual_load
            gc_info[gc_id] = (gc.cur_max_power, gc.cost)
        self.pred_ext_load[timestamp] = predicted_loads

        # reset charging station power
        for cs in self.world_state.charging_stations.values():
            cs.current_power = 0

        # gather current state of vehicles
        vehicles = {
            v_id: {
                "delta_energy": (v.desired_soc - v.battery.soc) / 100 * v.battery.capacity,
                "timesteps": 0
            } for v_id, v in self.world_state.vehicles.items()
        }

        # gather charging vehicles, external load and prices until all vehicles gone (24h max)
        future    = {}
        event_idx = 0
        timesteps_per_day = int(datetime.timedelta(days =1) / self.interval)
        timesteps_per_hour=     datetime.timedelta(hours=1) / self.interval

        cur_time = self.current_time - self.interval
        for _ in range(timesteps_per_day):
            cur_time += self.interval
            dts = str(cur_time)

            # get charging vehicles
            cur_vehicles = {gc: [] for gc in self.world_state.grid_connectors.keys()}
            for v_id, vehicle in self.world_state.vehicles.items():
                needs_charging = vehicle.battery.soc < vehicle.desired_soc
                still_present  = vehicle.estimated_time_of_departure > cur_time and vehicle.connected_charging_station is not None
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
            socs={vid: v.battery.soc for vid, v in self.world_state.vehicles.items()}
            return {'current_time': self.current_time, 'commands': {}, 'socs': socs}

        charging_stations = {}
        current_state = future[str(self.current_time)]
        # assign charging power by grid connector
        for gc_id, gc in self.world_state.grid_connectors.items():
            vehicles_present = current_state[gc_id]["vehicles"]
            available_power = current_state[gc_id]["power"]
            costs = [f[gc_id]["costs"] for f in future.values()]

            # sort charging vehicles by remaining time
            vehicles_present = sorted(vehicles_present, key=lambda v_id: vehicles[v_id]["timesteps"])

            for v_id in vehicles_present:
                vehicle = self.world_state.vehicles[v_id]
                delta_energy = vehicles[v_id]["delta_energy"]
                timesteps = vehicles[v_id]["timesteps"]
                cs_id = vehicle.connected_charging_station
                cs = self.world_state.charging_stations[cs_id]
                mean_power = (delta_energy / timesteps) * timesteps_per_hour

                # get normed costs in remaining timesteps
                norm_costs = [c for c in costs[:timesteps]]
                min_costs  = min(norm_costs)
                max_costs  = max(norm_costs)
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

        socs={vid: v.battery.soc for vid, v in self.world_state.vehicles.items()}
        return {'current_time': self.current_time, 'commands': charging_stations, 'socs': socs}
