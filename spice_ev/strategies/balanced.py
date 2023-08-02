from spice_ev.util import clamp_power, get_cost
from spice_ev.strategy import Strategy


class Balanced(Strategy):
    """ Charging with minimum required power to reach desired SoC during estimated parking time. """
    def __init__(self, components, start_time, **kwargs):
        # defaults
        super().__init__(components, start_time, **kwargs)
        self.description = "balanced"

    def step(self):
        """ Calculates charging power in each timestep.

        :return: current time and commands of the charging stations
        :rtype: dict
        """
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
            gc_id = cs.parent
            gc = self.world_state.grid_connectors[gc_id]
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
                # time until departure
                dt = vehicle.estimated_time_of_departure - self.current_time
                timesteps = -(dt // -self.interval)
                energy_needed = delta_soc*vehicle.battery.capacity / vehicle.battery.efficiency
                if timesteps > 0:
                    power = energy_needed / self.ts_per_hour / timesteps
                    power = clamp_power(power, vehicle, cs)
                else:
                    # past estimated time of departure, but still needs charging: greedy
                    power = clamp_power(gc.cur_max_power, vehicle, cs)

            # load with power
            avg_power = vehicle.battery.load(self.interval, target_power=power)['avg_power']
            charging_stations[cs_id] = gc.add_load(cs_id, avg_power)
            cs.current_power += avg_power
            if bat_power_used:
                avail_bat_power[gc_id] = max(avail_bat_power[gc_id] - avg_power, 0)

            # can active charging station bear minimum load?
            assert cs.max_power >= cs.current_power - self.EPS, (
                "{} - {} over maximum load ({} > {})".format(
                    self.current_time, cs_id, cs.current_power, cs.max_power))
            # can grid connector bear load?
            assert gc.cur_max_power >= gc.get_current_load() - self.EPS, (
                "{} - {} over maximum load ({} > {})".format(
                    self.current_time, cs.parent, gc.get_current_load(), gc.cur_max_power))

        # all vehicles loaded
        charging_stations.update(self.distribute_surplus_power())
        self.update_batteries()

        return {'current_time': self.current_time, 'commands': charging_stations}
