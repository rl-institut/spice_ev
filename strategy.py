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
    @abstractmethod
    def step(self, events=[]):
        raise NotImplementedError


class Greedy(Strategy):
    def __init__(self, constants):
        self.description = "greedy"
        self.last_time = None
        self.world_state = deepcopy(constants)
        self.world_state.future_events = []
        print(self.description)

    def step(self, time, event_list=[]):
        # update time and timedelta
        dt = time - (self.last_time if self.last_time else time)
        self.last_time = time

        self.world_state.future_events += event_list
        self.world_state.future_events.sort(key = lambda ev: ev.start_time)

        for ev in self.world_state.future_events:
            # ignore future events
            if ev.start_time > time:
                break

            # remove event from list
            self.world_state.future_events.pop(0)

            if type(ev) == events.ExternalLoad:
                connector = self.world_state.grid_connectors[ev.grid_connector_id]
                connector.current_loads[ev.name] = ev.value # not reset after last event
            elif type(ev) == events.GridOperatorSignal:
                connector = self.world_state.grid_connectors[ev.grid_connector_id]
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
            else:
                raise Exception("Unknown event type: {}".format(ev))

        for name, connector in self.world_state.grid_connectors.items():
            if not connector.cost:
                print("Warning: Connector {} has now associated costs at {}".format(name, time))

        for vehicle in self.world_state.vehicles.values():
            vehicle.soc -= vehicle.energy_delta
            vehicle.energy_delta = 0
            delta_soc = vehicle.desired_soc - vehicle.soc
            charging_station_id = vehicle.connected_charging_station
            if delta_soc > 0 and charging_station_id:
                charging_station = self.world_state.charging_stations[charging_station_id]
                # vehicle needs loading
                power_needed = delta_soc / 100 * vehicle.vehicle_type.capacity

                print(power_needed)
                vehicle.soc = vehicle.desired_soc
        #TODO return list of charging commands, +meta info
        return
