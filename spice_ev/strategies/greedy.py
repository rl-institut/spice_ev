from spice_ev.util import clamp_power, get_cost
from spice_ev.strategy import Strategy


class Greedy(Strategy):
    """ Uncontrolled charging with max power immediately after connecting to charging station.

    | Charges as much power as possible during each timestep until all desired SoC are reached.
    | No foresight, price does not matter for normal charging.
    | Stores surplus energy (local generation, low energy price) in stationary battery or vehicles.
    """

    def __init__(self, components, start_time, **kwargs):
        self.greedy_battery = False
        super().__init__(components, start_time, **kwargs)
        self.description = "greedy"

    def step(self):
        """ Calculate charging power in each timestep.

        :return: current time and commands of the charging stations
        :rtype: dict
        """

        # get power that can be drawn from battery in this timestep at each grid connector
        avail_bat_power = {gcID: 0 for gcID in self.world_state.grid_connectors.keys()}
        for battery in self.world_state.batteries.values():
            if battery.parent in avail_bat_power:
                avail_bat_power[battery.parent] += battery.get_available_power(self.interval)

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
            cs = self.world_state.charging_stations.get(cs_id)
            if cs is None:
                # unknown CS
                continue
            gc_id = cs.parent
            gc = self.world_state.grid_connectors[gc_id]
            gc_power_left = gc.cur_max_power - gc.get_current_load()
            delta_soc = vehicle.get_delta_soc()
            # initialize variables
            power = 0
            avg_power = 0

            if get_cost(1, gc.cost) <= self.PRICE_THRESHOLD:
                # low energy price: take max available power from GC (without stationary batteries)
                power = clamp_power(gc_power_left, vehicle, cs)
                # charge with power
                avg_power = vehicle.battery.load(self.interval, power)['avg_power']
            elif delta_soc > self.EPS:
                # vehicle needs charging: take max available power (with stationary batteries)
                power = gc_power_left + avail_bat_power[gc_id]
                power = clamp_power(power, vehicle, cs)
                # charge with power
                avg_power = vehicle.battery.load(
                    self.interval, max_power=power, target_soc=vehicle.desired_soc)['avg_power']

            # add load to charging station
            charging_stations[cs_id] = gc.add_load(cs_id, avg_power)
            cs.current_power += avg_power

        # all vehicles charged
        charging_stations.update(self.distribute_surplus_power())
        if self.greedy_battery:
            # use remaining power to charge batteries
            for b_id, battery in self.world_state.batteries.items():
                gc = self.world_state.grid_connectors[battery.parent]
                gc_power_left = gc.cur_max_power - gc.get_current_load()
                p = 0
                if gc_power_left > battery.min_charging_power:
                    # GC power below max power: charge battery
                    p = battery.load(self.interval, target_power=gc_power_left)['avg_power']
                elif gc_power_left < 0:
                    # GC draws power: support with battery
                    p = -battery.unload(self.interval, target_power=-gc_power_left)['avg_power']
                gc.add_load(b_id, p)
        else:
            # normal charging (charge if cheap/surplus, otherwise support GC)
            self.update_batteries()

        return {'current_time': self.current_time, 'commands': charging_stations}
