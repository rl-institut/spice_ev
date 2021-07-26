from copy import deepcopy
from importlib import import_module

from src import events


def class_from_str(strategy_name):
    import_name = strategy_name.lower()
    class_name = "".join([s.capitalize() for s in strategy_name.split('_')])
    module = import_module('src.strategies.' + import_name)
    return getattr(module, class_name)


class Strategy():
    """ strategy
    """

    def __init__(self, constants, start_time, **kwargs):
        self.world_state = deepcopy(constants)
        self.world_state.future_events = []
        self.interval = kwargs.get('interval')  # required
        self.current_time = start_time - self.interval
        # relative allowed difference between battery SoC and desired SoC when leaving
        self.margin = 0.05
        self.allow_negative_soc = False
        # tolerance for floating point comparison
        self.EPS = 1e-5
        # update optional
        for k, v in kwargs.items():
            setattr(self, k, v)

    def step(self, event_list=[]):
        self.current_time += self.interval

        self.world_state.future_events += event_list
        self.world_state.future_events.sort(key=lambda ev: ev.start_time)

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
                assert ev.name not in self.world_state.charging_stations, (
                    "External load must not be from charging station")
                connector.current_loads[ev.name] = ev.value  # not reset after last event
            elif type(ev) == events.EnergyFeedIn:
                assert ev.name not in self.world_state.charging_stations, (
                    "Energy feed-in must not be from charging station")
                connector = self.world_state.grid_connectors[ev.grid_connector_id]
                connector.current_loads[ev.name] = -ev.value
            elif type(ev) == events.GridOperatorSignal:
                connector = self.world_state.grid_connectors[ev.grid_connector_id]
                if ev.cost:
                    # set power cost
                    connector.cost = ev.cost
                # set max power from event
                if connector.max_power:
                    if ev.max_power is None:
                        # event max power not set: reset to connector power
                        connector.cur_max_power = connector.max_power
                    else:
                        connector.cur_max_power = min(connector.max_power, ev.max_power)
                else:
                    # connector max power not set
                    connector.cur_max_power = ev.max_power

            elif type(ev) == events.VehicleEvent:
                vehicle = self.world_state.vehicles[ev.vehicle_id]
                for k, v in ev.update.items():
                    setattr(vehicle, k, v)
                if ev.event_type == "departure":
                    vehicle.connected_charging_station = None
                    assert vehicle.battery.soc >= (1-self.margin)*vehicle.desired_soc - self.EPS, (
                        "{}: Vehicle {} is below desired SOC ({} < {})".format(
                            ev.start_time.isoformat(), ev.vehicle_id,
                            vehicle.battery.soc, vehicle.desired_soc))
                elif ev.event_type == "arrival":
                    assert vehicle.connected_charging_station is not None
                    assert hasattr(vehicle, 'soc_delta')
                    vehicle.battery.soc += vehicle.soc_delta
                    if vehicle.battery.soc + self.EPS < 0:
                        if self.allow_negative_soc:
                            print('Warning: SOC of vehicle {} became negative at {}. SOC is {},'
                                  'continuing with SOC = 0'
                                  .format(ev.vehicle_id, self.current_time, vehicle.battery.soc))
                            vehicle.battery.soc = 0
                        else:
                            raise RuntimeError(
                                'SOC of vehicle {} should not be negative. '
                                'SOC is {}, soc_delta was {}'
                                .format(ev.vehicle_id, vehicle.battery.soc, vehicle.soc_delta))
                    delattr(vehicle, 'soc_delta')
            else:
                raise Exception("Unknown event type: {}".format(ev))

        for name, connector in self.world_state.grid_connectors.items():
            # reset charging stations and battery loads at grid connector
            for load_name in list(connector.current_loads.keys()):
                if load_name in self.world_state.charging_stations.keys():
                    del connector.current_loads[load_name]
                if load_name in self.world_state.batteries.keys():
                    del connector.current_loads[load_name]

            # check for associated costs
            if not connector.cost:
                raise Exception(
                    "Connector {} has no associated costs at {}".format(name, self.current_time))
