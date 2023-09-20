from spice_ev.util import clamp_power, get_cost
from spice_ev.strategy import Strategy


class Greedy(Strategy):
    """ Uncontrolled charging with max power immediately after connecting to charging station.

    | Charges as much power as possible during each timestep until all desired SoC are reached.
    | No foresight, price does not matter for normal charging.
    | Stores surplus energy (local generation, low energy price) in stationary battery or vehicles.
    """

    def __init__(self, components, start_time, **kwargs):
        self.PRICE_THRESHOLD = 0.001  # EUR/kWh
        super().__init__(components, start_time, **kwargs)
        self.description = "greedy"

    def step(self):
        """ Calculate charging power in each timestep.

        :return: current time and commands of the charging stations
        :rtype: dict
        """

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

            charging_stations, avail_bat_power[cs.parent] = charge_vehicle(
                self, cs, gc, vehicle, cs_id, charging_stations, avail_bat_power[cs.parent])
        # all vehicles charged
        charging_stations.update(self.distribute_surplus_power())
        self.update_batteries()

        return {'current_time': self.current_time, 'commands': charging_stations}


def charge_vehicle(strategy, cs, gc, vehicle, cs_id, charging_stations, avail_bat_power):
    """ Charge one vehicle with greedy strategy.

    :param strategy: current world state
    :type strategy: Strategy
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
    if get_cost(1, gc.cost) <= strategy.PRICE_THRESHOLD:
        # low energy price: take max available power from GC without batteries
        power = clamp_power(gc_power_left, vehicle, cs)
        avg_power = vehicle.battery.charge(strategy.interval, max_power=power)['avg_power']
    elif vehicle.get_delta_soc() > 0:
        # vehicle needs charging: take max available power (with batteries)
        # limit to desired SoC
        power = gc_power_left + avail_bat_power
        power = clamp_power(power, vehicle, cs)
        avg_power = vehicle.battery.charge(
            strategy.interval, max_power=power, target_soc=vehicle.desired_soc)['avg_power']
        bat_power_used = True

    # update CS and GC
    charging_stations[cs_id] = gc.add_load(cs_id, avg_power)
    cs.current_power += avg_power
    if bat_power_used:
        avail_bat_power = max(avail_bat_power - avg_power, 0)

    return charging_stations, avail_bat_power
