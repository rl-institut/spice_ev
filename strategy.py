from copy import deepcopy

import battery
import events
import loading_curve

def class_from_str(strategy_name):
    if strategy_name == 'greedy':
        return Greedy
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

        # Add battery object to vehicles
        for v in self.world_state.vehicles.values():
            v.battery = battery.Battery(
                v.vehicle_type.capacity,
                v.vehicle_type.charging_curve,
                v.soc,
            )
            del v.soc

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
                    assert vehicle.battery.soc >= vehicle.desired_soc * 0.99, "{}: Vehicle {} is below desired SOC ({} < {})".format(ev.start_time.isoformat(), ev.vehicle_id, vehicle.soc, vehicle.desired_soc)
                elif ev.event_type == "arrival":
                    assert vehicle.connected_charging_station is not None
                    assert hasattr(vehicle, 'soc_delta')
                    vehicle.battery.soc += vehicle.soc_delta
                    assert vehicle.battery.soc >= 0, 'SOC of vehicle {} should not be negative. SOC is {}, soc_delta was {}'.format(ev.vehicle_id, vehicle.soc, vehicle.soc_delta)
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
