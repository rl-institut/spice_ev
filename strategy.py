from copy import deepcopy

import events

def class_from_str(strategy_name):
    if strategy_name == 'greedy':
        return Greedy
    elif strategy_name == 'parity':
        return Parity
    elif strategy_name == 'balanced':
        return Balanced
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
                    assert vehicle.battery.soc >= vehicle.desired_soc * 0.99, "{}: Vehicle {} is below desired SOC ({} < {})".format(ev.start_time.isoformat(), ev.vehicle_id, vehicle.battery.soc, vehicle.desired_soc)
                elif ev.event_type == "arrival":
                    assert vehicle.connected_charging_station is not None
                    assert hasattr(vehicle, 'soc_delta')
                    vehicle.battery.soc += vehicle.soc_delta
                    assert vehicle.battery.soc >= 0, 'SOC of vehicle {} should not be negative. SOC is {}, soc_delta was {}'.format(ev.vehicle_id, vehicle.battery.soc, vehicle.soc_delta)
                    delattr(vehicle, 'soc_delta')


            else:
                raise Exception("Unknown event type: {}".format(ev))

        for name, connector in self.world_state.grid_connectors.items():
            if not connector.cost:
                raise Exception("Warning: Connector {} has no associated costs at {}".format(name, time))


class Greedy(Strategy):
    def __init__(self, constants, start_time, interval):
        super().__init__(constants, start_time, interval)
        self.description = "greedy"


    def step(self, event_list=[]):
        super().step(event_list)

        grid_connectors = {name: {'cur_max_power': gc.cur_max_power, 'current_load': sum(gc.current_loads.values())} for name, gc in self.world_state.grid_connectors.items()}
        charging_stations = {}
        socs = {}

        for vehicle_id in sorted(self.world_state.vehicles):
            vehicle = self.world_state.vehicles[vehicle_id]
            delta_soc = vehicle.desired_soc - vehicle.battery.soc
            charging_station_id = vehicle.connected_charging_station
            if delta_soc > 0 and charging_station_id:
                charging_station = self.world_state.charging_stations[charging_station_id]
                # vehicle needs loading
                grid_connector = grid_connectors[charging_station.parent]
                gc_power_left = grid_connector['cur_max_power'] - grid_connector['current_load']
                cs_power_left = charging_station.max_power - charging_stations.get(charging_station_id, 0)
                max_power = min(cs_power_left, gc_power_left)

                load_result = vehicle.battery.load(self.interval, max_power)
                avg_power = load_result['avg_power']

                grid_connectors[charging_station.parent]['current_load'] += avg_power

                if charging_station_id in charging_stations:
                    charging_stations[charging_station_id] += avg_power
                else:
                    charging_stations[charging_station_id] = avg_power

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

        grid_connectors = {name: {
            'cur_max_power': gc.cur_max_power,
            'current_load': sum(gc.current_loads.values()),
            'charging_stations': {}
        } for name, gc in self.world_state.grid_connectors.items()}
        charging_stations = {}

        # gather all vehicles in need of charge
        for vehicle_id, vehicle in self.world_state.vehicles.items():
            delta_soc = vehicle.desired_soc - vehicle.battery.soc
            cs_id = vehicle.connected_charging_station
            if delta_soc > 0 and cs_id:
                cs = self.world_state.charging_stations[cs_id]
                gc = grid_connectors[cs.parent]
                # vehicle needs loading
                charging_stations[cs_id] = 0
                if cs_id in gc['charging_stations']:
                    gc['charging_stations'][cs_id].append(vehicle_id)
                else:
                    gc['charging_stations'][cs_id] = [vehicle_id]

        # distribute power of each grid connector
        for gc in grid_connectors.values():
            gc_power_left = gc['cur_max_power'] - gc['current_load']
            for cs_id, vehicles in gc['charging_stations'].items():
                cs = self.world_state.charging_stations[cs_id]

                # find minimum of distributed power and charging station power
                # guaranteed to have one requesting charging station
                gc_dist_power = gc_power_left / len(gc['charging_stations'])
                gc_dist_power = min(gc_dist_power, cs.max_power)
                # CS guaranteed to have one requesting vehicle
                cs_dist_power = gc_dist_power / len(gc['charging_stations'][cs_id])

                # distribute power within CS
                for vehicle_id in vehicles:
                    vehicle = self.world_state.vehicles[vehicle_id]
                    # load battery
                    load_result = vehicle.battery.load(self.interval, cs_dist_power)
                    avg_power = load_result['avg_power']
                    charging_stations[cs_id] += avg_power
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
                    timedelta = vehicle.estimated_time_of_departure - self.current_time
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
                        charged_soc = vehicle.battery.load(timedelta, power)["soc_delta"]
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

                # load with power
                avg_power = vehicle.battery.load(self.interval, power)['avg_power']
                assert vehicle.battery.soc <= 100
                assert vehicle.battery.soc >= 0, 'SOC of {} is {}'.format(vehicle_id, vehicle.battery.soc)
                if cs_id in charging_stations:
                    charging_stations[cs_id] += avg_power
                else:
                    charging_stations[cs_id] = avg_power

        # set current_power of all unconnected CS to 0
        for cs_id, cs in self.world_state.charging_stations.items():
            if cs_id not in charging_stations:
                cs.current_power = 0

        socs={vid: v.battery.soc for vid, v in self.world_state.vehicles.items()}

        return {'current_time': self.current_time, 'commands': charging_stations, 'socs': socs}
