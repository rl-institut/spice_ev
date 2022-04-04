from src.util import clamp_power, get_cost
from src.strategy import Strategy


class Balanced(Strategy):
    """Balanced strategy

    Charging strategy that calculates the minimum required charging power to
    arrive at the desired SOC during the estimated parking time for each vehicle.
    """
    def __init__(self, constants, start_time, **kwargs):
        # defaults
        self.ITERATIONS = 12
        self.PRICE_THRESHOLD = 0.001  # EUR/kWh

        super().__init__(constants, start_time, **kwargs)
        self.description = "balanced"

    def step(self, event_list=[]):
        """
        Calculates charging in each timestep.
        :param event_list: List of events
        :type event_list: list
        :return: current time and commands of the charging stations
        :rtype: dict
        """
        super().step(event_list)

        # get power that can be drawn from battery in this timestep
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
            # get vehicle
            vehicle = self.world_state.vehicles[vehicle_id]
            cs_id = vehicle.connected_charging_station
            if cs_id is None:
                # not connected
                continue
            # get connected charging station
            cs = self.world_state.charging_stations[cs_id]
            gc = self.world_state.grid_connectors[cs.parent]

            charging_stations, avail_bat_power[cs.parent] = \
                load_vehicle(self, cs, gc, vehicle, cs_id, charging_stations,
                             avail_bat_power[cs.parent])

        # all vehicles loaded
        charging_stations.update(self.distribute_surplus_power())
        self.update_batteries()

        return {'current_time': self.current_time, 'commands': charging_stations}


def load_vehicle(self, cs, gc, vehicle, cs_id, charging_stations, avail_bat_power):
    """
    Load one vehicle with balanced strategy
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
    bat_power_used = False
    delta_soc = vehicle.get_delta_soc()

    if get_cost(1, gc.cost) <= self.PRICE_THRESHOLD:
        # low energy price: take max available power from GC without batteries
        power = clamp_power(gc_power_left, vehicle, cs)
    elif delta_soc > self.EPS:
        # vehicle needs charging: compute minimum required power
        bat_power_used = True
        # get limits
        min_power = max(vehicle.vehicle_type.min_charging_power, cs.min_power)
        max_power = gc_power_left + avail_bat_power
        max_power = min(max_power, vehicle.vehicle_type.charging_curve.max_power)
        max_power = clamp_power(max_power, vehicle, cs)
        # time until departure
        dt = vehicle.estimated_time_of_departure - self.current_time
        old_soc = vehicle.battery.soc
        idx = 0
        safe = False
        # converge to optimal power for the duration
        # at least ITERATIONS cycles
        # must end with slightly too much power used
        # abort if min_power == max_power (e.g. unrealistic goal)
        while (idx < self.ITERATIONS or not safe) and max_power - min_power > self.EPS:
            idx += 1
            # get new power value (binary search: use average)
            power = (max_power + min_power) / 2
            # load whole time with same power
            charged_soc = vehicle.battery.load(dt, power)["soc_delta"]
            # reset SOC
            vehicle.battery.soc = old_soc

            if delta_soc - charged_soc > self.EPS:  # charged_soc < delta_soc
                # power not enough
                safe = False
                min_power = power
            else:  # charged_soc >= delta_soc:
                # power too much or just right (may be possible with less power)
                safe = True
                max_power = power

    # load with power
    avg_power = vehicle.battery.load(self.interval, power)['avg_power']
    charging_stations[cs_id] = gc.add_load(cs_id, avg_power)
    cs.current_power += avg_power
    if bat_power_used:
        avail_bat_power = max(avail_bat_power - avg_power, 0)

    # can active charging station bear minimum load?
    assert cs.max_power >= cs.current_power - self.EPS, (
        "{} - {} over maximum load ({} > {})".format(
            self.current_time, cs_id, cs.current_power, cs.max_power))
    # can grid connector bear load?
    assert gc.cur_max_power >= gc.get_current_load() - self.EPS, (
        "{} - {} over maximum load ({} > {})".format(
            self.current_time, cs.parent, gc.get_current_load(), gc.cur_max_power))

    return charging_stations, avail_bat_power
