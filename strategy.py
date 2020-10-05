from abc import ABC, abstractmethod
from copy import deepcopy

import events

def class_from_str(strategy_name):
    if strategy_name == 'greedy':
        return Greedy
    else:
        raise Exception('unknown strategy with name {}'.format(strategy_name))


class Strategy(ABC):
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
                        connector.cur_max_power = max(connector.max_power, ev.max_power)
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
                elif ev.event_type == "arrival":
                    assert vehicle.connected_charging_station is not None
                    assert hasattr(vehicle, 'soc_delta')
                    vehicle.soc += vehicle.soc_delta
                    delattr(vehicle, 'soc_delta')
                    assert(vehicle.soc >= 0, 'SOC of vehicle {} negative :('.format(ev.vehicle_id))


            else:
                raise Exception("Unknown event type: {}".format(ev))

        for name, connector in self.world_state.grid_connectors.items():
            if not connector.cost:
                raise Exception("Warning: Connector {} has no associated costs at {}".format(name, time))


class Greedy(Strategy):
    def __init__(self, constants, start_time, interval):
        Strategy.__init__(self, constants, start_time, interval)
        self.description = "greedy"
        print(self.description)

    def step(self, event_list=[]):
        Strategy.step(self, event_list)

        for vehicle in self.world_state.vehicles.values():
            delta_soc = vehicle.desired_soc - vehicle.soc
            charging_station_id = vehicle.connected_charging_station
            if delta_soc > 0 and charging_station_id:
                charging_station = self.world_state.charging_stations[charging_station_id]
                # vehicle needs loading
                #TODO compute charging losses and use charging curve
                power_needed = delta_soc / 100 * vehicle.vehicle_type.capacity

                print(power_needed)
                vehicle.soc = vehicle.desired_soc
        #TODO return list of charging commands, +meta info
        return {'current_time': self.current_time}
