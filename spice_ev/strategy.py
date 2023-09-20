from copy import deepcopy
from importlib import import_module
from warnings import warn

from spice_ev import events
from spice_ev.util import get_cost, clamp_power


def class_from_str(strategy_name):
    import_name = strategy_name.lower()
    class_name = "".join([s.capitalize() for s in strategy_name.split('_')])
    module = import_module('spice_ev.strategies.' + import_name)
    return getattr(module, class_name)


class Strategy():
    """ Parent class for the individual strategies.

    :param components: class containing the components
    :type components: class
    :param start_time: start time of the simulation
    :type start_time: datetime
    :param interval: interval of one timestep of the simulation (e.g. 15 min)
    :type interval: timedelta
    :param kwargs: other input parameters
    :type kwargs: dict
    """

    def __init__(self, components, start_time, **kwargs):

        self.world_state = deepcopy(components)
        self.world_state.future_events = []
        self.interval = kwargs.get('interval')  # required
        self.current_time = start_time - self.interval
        # relative allowed difference between battery SoC and desired SoC when leaving
        self.margin = 0.1
        self.PRICE_THRESHOLD = 0
        self.ALLOW_NEGATIVE_SOC = False
        self.RESET_NEGATIVE_SOC = False
        self.V2G_POWER_FACTOR = 0.5
        # check if strategy uses grid signals & enable/disable plotting of schedule or window
        self.uses_schedule = False
        self.uses_window = False
        # tolerance for floating point comparison
        self.EPS = 1e-5
        # Reduce available power at each charging station to given fraction (0 - 1)
        for cs in self.world_state.charging_stations.values():
            cs.max_power = kwargs.get('CONCURRENCY', 1.0) * cs.max_power
        # dummy description (should be set in actual strategies)
        self.description = None
        # update optional
        for k, v in kwargs.items():
            setattr(self, k, v)
        # everything below can not be set by user
        # for each vehicle, save timestamps when SoC becomes negative
        self.negative_soc_tracker = {}
        # count number of times SoC is below desired SoC on departure (used in report)
        self.desired_counter = 0
        # count number of times SoC is below desired SoC (with margin) on departure
        self.margin_counter = 0

    def step(self, event_list=[]):
        """ Prepare next timestep for specific charging strategy.

        Processes next events, makes some sanity checks and resets loads at grid connectors.

        :param event_list: List of events
        :type event_list: list
        :raises Exception: if an unknown event type is encountered or
            a GC has neither associated costs nor schedule at any time
        :raises RuntimeError: if any vehicle SoC becomes negative
            (use *ALLOW_NEGATIVE_SOC* to suppress)
        """

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

            if type(ev) is events.FixedLoad:
                connector = self.world_state.grid_connectors[ev.grid_connector_id]
                assert ev.name not in self.world_state.charging_stations, (
                    "Fixed load must not be from charging station")
                connector.current_loads[ev.name] = ev.value  # not reset after last event
            elif type(ev) is events.LocalEnergyGeneration:
                assert ev.name not in self.world_state.charging_stations, (
                    "Local energy generation must not be from charging station")
                connector = self.world_state.grid_connectors[ev.grid_connector_id]
                connector.current_loads[ev.name] = -ev.value
            elif type(ev) is events.GridOperatorSignal:
                connector = self.world_state.grid_connectors[ev.grid_connector_id]
                if ev.cost is not None:
                    # set power cost
                    connector.cost = ev.cost
                if ev.target is not None:
                    # set target power from schedule
                    connector.target = ev.target
                if ev.window is not None:
                    connector.window = ev.window
                # set max power from event
                if connector.max_power:
                    if ev.max_power is not None:
                        connector.cur_max_power = min(connector.max_power, ev.max_power)
                else:
                    # connector max power not set
                    connector.cur_max_power = ev.max_power
            elif type(ev) is events.VehicleEvent:
                vehicle = self.world_state.vehicles.get(ev.vehicle_id)
                if vehicle is None:
                    # skip events without vehicle
                    continue
                # update vehicle attributes
                for k, v in ev.update.items():
                    setattr(vehicle, k, v)
                if ev.event_type == "departure":
                    vehicle.estimated_time_of_departure = None
                    if ev.start_time < self.current_time - self.interval:
                        # event from the past: simulate optimal charging
                        vehicle.battery.soc = vehicle.desired_soc
                    if vehicle.connected_charging_station is not None:
                        # if connected, check that vehicle has charged enough
                        self.desired_counter += vehicle.battery.soc < vehicle.desired_soc - self.EPS
                        if 0 <= vehicle.battery.soc < (1-self.margin)*vehicle.desired_soc-self.EPS:
                            # not charged enough: write warning
                            self.margin_counter += 1
                            warn("{}: Vehicle {} is below desired SOC ({} < {})".format(
                                ev.start_time.isoformat(), ev.vehicle_id,
                                vehicle.battery.soc, vehicle.desired_soc))
                        # vehicle leaves: disconnect vehicle
                        vehicle.connected_charging_station = None
                elif ev.event_type == "arrival":
                    # vehicle arrives
                    assert hasattr(vehicle, 'soc_delta')
                    # soc_delta always negative
                    vehicle.battery.soc += vehicle.soc_delta
                    if vehicle.battery.soc + self.EPS < 0:
                        # vehicle was not charged enough to make trip
                        time_str = self.current_time.isoformat()
                        if ev.vehicle_id not in self.negative_soc_tracker.keys():
                            self.negative_soc_tracker[ev.vehicle_id] = [time_str]
                        else:
                            self.negative_soc_tracker[ev.vehicle_id].append(time_str)
                        if self.ALLOW_NEGATIVE_SOC:
                            warn('SOC of vehicle {} became negative at {}. SOC is {}'
                                 .format(ev.vehicle_id, self.current_time, vehicle.battery.soc),
                                 # settings stack level high to avoid confusing
                                 # info about origin of error (e.g. filename, lineno)
                                 stacklevel=100)
                            if self.RESET_NEGATIVE_SOC:
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

            # check GC: must have costs (dict, may be empty) or schedule (float/None)
            if not connector.cost and connector.target is None:
                raise Exception(
                    "Connector {} has neither associated costs nor schedule at {}"
                    .format(name, self.current_time))

    def distribute_surplus_power(self):
        """ Distribute surplus power to vehicles.

        :return: charging commands
        :rtype: dict
        """

        commands = dict()
        gc_cheap = {
            gc_id: get_cost(1, gc.cost) <= self.PRICE_THRESHOLD
            for gc_id, gc in self.world_state.grid_connectors.items()}
        for vehicle in self.world_state.vehicles.values():
            cs_id = vehicle.connected_charging_station
            if cs_id is None:
                continue
            cs = self.world_state.charging_stations[cs_id]
            gc = self.world_state.grid_connectors[cs.parent]
            gc_surplus = -gc.get_current_load()
            if gc_surplus > self.EPS:
                # surplus power
                power = clamp_power(gc_surplus, vehicle, cs)
                avg_power = vehicle.battery.charge(self.interval, max_power=power)['avg_power']
                commands[cs_id] = gc.add_load(cs_id, avg_power)
                cs.current_power += avg_power
            elif (vehicle.get_delta_soc() < -self.EPS
                    and vehicle.vehicle_type.v2g
                    and cs.current_power < self.EPS
                    and not gc_cheap[cs.parent]):
                # GC draws power, surplus in vehicle and V2G capable: support GC
                discharge_power = min(
                    -gc_surplus,
                    vehicle.battery.charging_curve.max_power*vehicle.vehicle_type.v2g_power_factor)
                target_soc = max(vehicle.desired_soc, self.DISCHARGE_LIMIT)
                avg_power = vehicle.battery.discharge(
                    self.interval, max_power=discharge_power, target_soc=target_soc)['avg_power']
                commands[cs_id] = gc.add_load(cs_id, -avg_power)
                cs.current_power -= avg_power
        return commands

    def update_batteries(self):
        """ Charge/discharge batteries. In-place, no input/output """
        gc_cheap = {
            gc_id: get_cost(1, gc.cost) <= self.PRICE_THRESHOLD
            for gc_id, gc in self.world_state.grid_connectors.items()}
        for b_id, battery in self.world_state.batteries.items():
            gc = self.world_state.grid_connectors[battery.parent]
            gc_current_load = gc.get_current_load()
            if gc_cheap[battery.parent]:
                # low price: charge with full power
                power = gc.cur_max_power - gc_current_load
                power = 0 if power < battery.min_charging_power else power
                avg_power = battery.charge(self.interval, max_power=power)['avg_power']
                gc.add_load(b_id, avg_power)
            elif gc_current_load < 0:
                # surplus energy: charge
                power = -gc_current_load
                power = 0 if power < battery.min_charging_power else power
                avg_power = battery.charge(self.interval, target_power=power)['avg_power']
                gc.add_load(b_id, avg_power)
            else:
                # GC draws power: use stored energy to support GC
                bat_power = battery.discharge(
                    self.interval, target_power=gc_current_load)['avg_power']
                gc.add_load(b_id, -bat_power)

    def apply_battery_losses(self):
        """ Regardless of specific strategy, reduce SoC of lossy batteries. """
        for battery in (
                        list(self.world_state.batteries.values()) +
                        [v.battery for v in self.world_state.vehicles.values()]):
            if battery.loss_rate:
                relative_loss = battery.loss_rate.get("relative", 0)
                battery.soc *= 1 - relative_loss/100
                fixed_relative_loss = battery.loss_rate.get("fixed_relative", 0)
                battery.soc -= fixed_relative_loss / 100
                fixed_absolute_loss = battery.loss_rate.get("fixed_absolute", 0)
                battery.soc -= fixed_absolute_loss / battery.capacity
                # can only discharge, but not become negative
                battery.soc = max(battery.soc, 0)
