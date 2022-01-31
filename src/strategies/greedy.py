from src.util import clamp_power, get_cost
from src.strategy import Strategy


class Greedy(Strategy):
    """ Greedy strategy

    Basic, dumb strategy.

    Charges as much power as possible during each timestep until all desired SOC are reached.
    No foresight, price does not matter for normal charging.
    Can store surplus energy (feed-in or low energy price) in stationary battery or vehicles.
    """
    def __init__(self, constants, start_time, **kwargs):
        self.PRICE_THRESHOLD = 0.001  # EUR/kWh
        super().__init__(constants, start_time, **kwargs)
        self.description = "greedy"

    def step(self, event_list=[]):
        """
        Calculates charging in each timestep.

        :param event_list: List of events
        :type event_list: list
        :return: current time and commands of the charging stations
        :rtype: dict
        """
        super().step(event_list)

        # get power that can be drawn from battery in this timestep at each grid connector
        avail_bat_power = {}
        for gcID, gc in self.world_state.grid_connectors.items():
            avail_bat_power[gcID] = 0
            for bat in self.world_state.batteries.values():
                if bat.parent == gcID:
                    avail_bat_power[gcID] += bat.get_available_power(self.interval)

        # dict to hold charging commands
        charging_stations = {}
        # reset charging station power (nothing charged yet in this timestep)
        for cs in self.world_state.charging_stations.values():
            cs.current_power = 0

        for vehicle_id in sorted(self.world_state.vehicles):
            vehicle = self.world_state.vehicles[vehicle_id]
            cs_id = vehicle.connected_charging_station
            if cs_id is None:
                # not connected
                continue

            cs = self.world_state.charging_stations[cs_id]
            gc = self.world_state.grid_connectors[cs.parent]

            charging_stations = load_vehicle(self, cs, gc, vehicle, cs_id, charging_stations,
                                             avail_bat_power[cs.parent])

        # all vehicles loaded
        # distribute surplus power to vehicles
        # power is clamped to CS max_power (with concurrency, see init)
        for vehicle_id in sorted(self.world_state.vehicles):
            vehicle = self.world_state.vehicles[vehicle_id]
            cs_id = vehicle.connected_charging_station
            if cs_id is None:
                continue
            cs = self.world_state.charging_stations[cs_id]
            gc = self.world_state.grid_connectors[cs.parent]

            add_surplus_to_vehicle(self, cs, gc, vehicle, cs_id, charging_stations)

        # charge/discharge batteries
        load_batteries(self)

        return {'current_time': self.current_time, 'commands': charging_stations}


def load_vehicle(self, cs, gc, vehicle, cs_id, charging_stations, avail_bat_power):
    """
    Load one vehicle with greedy strategy

    :param cs: charging station dict
    :type cs: dict
    :param gc: grid connector dict
    :type gc: dict
    :param vehicle: vehicle dict
    :type vehicle: dict
    :param cs_id: name of the charging station
    :type cs_id: str
    :param charging_stations: charging stations
    :type charging_stations: dict
    :param avail_bat_power: available battery power of the gc
    :type avail_bat_power: float
    :return: current time and commands of the charging stations
    :rtype: dict
    """
    gc_power_left = gc.cur_max_power - gc.get_current_load()
    power = 0
    avg_power = 0
    bat_power_used = False
    if get_cost(1, gc.cost) <= self.PRICE_THRESHOLD:
        # low energy price: take max available power from GC without batteries
        power = clamp_power(gc_power_left, vehicle, cs)
        avg_power = vehicle.battery.load(self.interval, power)['avg_power']
    elif vehicle.get_delta_soc() > 0:
        # vehicle needs charging: take max available power (with batteries)
        # limit to desired SoC
        power = gc_power_left + avail_bat_power
        power = clamp_power(power, vehicle, cs)
        avg_power = vehicle.battery.load(
            self.interval, power, target_soc=vehicle.desired_soc)['avg_power']
        bat_power_used = True

    # update CS and GC
    charging_stations[cs_id] = gc.add_load(cs_id, avg_power)
    cs.current_power += avg_power
    if bat_power_used:
        avail_bat_power = max(avail_bat_power - avg_power, 0)

    return charging_stations


def add_surplus_to_vehicle(self, cs, gc, vehicle, cs_id, charging_stations):
    """
    Add left over energy to vehicle

    :param cs: charging station dict
    :type cs: dict
    :param gc: grid connector dict
    :type gc: dict
    :param vehicle: vehicle dict
    :type vehicle: dict
    :param cs_id: name of the charging station
    :type cs_id: str
    :param charging_stations: charging stations
    :type charging_stations: dict
    :return: current time and commands of the charging stations
    :rtype: dict
    """

    if gc.get_current_load() < 0:
        # surplus power
        power = clamp_power(-gc.get_current_load(), vehicle, cs)
        avg_power = vehicle.battery.load(self.interval, power)['avg_power']
        charging_stations[cs_id] = gc.add_load(cs_id, avg_power)
        cs.current_power += avg_power
    elif (vehicle.get_delta_soc() < 0
          and vehicle.vehicle_type.v2g
          and cs.current_power < self.EPS
          and get_cost(1, gc.cost) > self.PRICE_THRESHOLD):
        # GC draws power, surplus in vehicle and V2G capable: support GC
        discharge_power = min(
            gc.get_current_load(),
            vehicle.battery.loading_curve.max_power * self.V2G_POWER_FACTOR)
        target_soc = max(vehicle.desired_soc, self.DISCHARGE_LIMIT)
        avg_power = vehicle.battery.unload(
            self.interval, discharge_power, target_soc)['avg_power']
        charging_stations[cs_id] = gc.add_load(cs_id, -avg_power)
        cs.current_power -= avg_power

    return charging_stations


def load_batteries(self):
    """
    Load batteries with greedy strategy
    """

    for b_id, battery in self.world_state.batteries.items():
        gc = self.world_state.grid_connectors[battery.parent]
        if get_cost(1, gc.cost) <= self.PRICE_THRESHOLD:
            # low price: charge with full power
            power = gc.cur_max_power - gc.get_current_load()
            power = 0 if power < battery.min_charging_power else power
            avg_power = battery.load(self.interval, power)['avg_power']
            gc.add_load(b_id, avg_power)
        elif gc.get_current_load() < 0:
            # surplus energy: charge
            power = -gc.get_current_load()
            power = 0 if power < battery.min_charging_power else power
            avg_power = battery.load(self.interval, power)['avg_power']
            gc.add_load(b_id, avg_power)
        else:
            # GC draws power: use stored energy to support GC
            bat_power = battery.unload(self.interval, gc.get_current_load())['avg_power']
            gc.add_load(b_id, -bat_power)
