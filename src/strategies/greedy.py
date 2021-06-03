from src.util import clamp_power, get_cost
from src.strategy import Strategy


class Greedy(Strategy):
    """
    Basic, dumb strategy.

    Charges as much power as possible during each timestep until all desired SOC are reached.
    No foresight, price does not matter for normal charging.
    Can store surplus energy (feed-in or low energy price) in stationary battery or vehicles.
    Can set CONCURRENCY, so each CP can only give a fraction of its maximum power.
    """
    def __init__(self, constants, start_time, **kwargs):
        self.CONCURRENCY = 1.0
        self.PRICE_THRESHOLD = 0.001  # EUR/kWh
        super().__init__(constants, start_time, **kwargs)
        self.description = "greedy"

        # concurrency: set fraction of maximum available power at each charging station
        for cs in self.world_state.charging_stations.values():
            cs.max_power = self.CONCURRENCY * cs.max_power

    def step(self, event_list=[]):
        super().step(event_list)

        # get power that can be drawn from battery in this timestep
        avail_bat_power = sum([
            bat.get_available_power(self.interval) for bat in self.world_state.batteries.values()])

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
            gc_power_left = gc.cur_max_power - gc.get_current_load()
            power = 0
            bat_power_used = False
            if get_cost(1, gc.cost) <= self.PRICE_THRESHOLD:
                # low energy price: take max available power from GC without batteries
                power = clamp_power(gc_power_left, vehicle, cs)
            elif vehicle.get_delta_soc() > 0:
                # vehicle needs charging: take max available power (with batteries)
                power = gc_power_left + avail_bat_power
                power = clamp_power(power, vehicle, cs)
                bat_power_used = True

            # charge vehicle
            avg_power = vehicle.battery.load(self.interval, power)['avg_power']
            charging_stations[cs_id] = gc.add_load(cs_id, avg_power)
            cs.current_power += avg_power
            if bat_power_used:
                avail_bat_power = max(avail_bat_power - avg_power, 0)

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
            if gc.get_current_load() < 0:
                # surplus power
                power = clamp_power(-gc.get_current_load(), vehicle, cs)
                avg_power = vehicle.battery.load(self.interval, power)['avg_power']
                charging_stations[cs_id] = gc.add_load(cs_id, avg_power)
                cs.current_power += avg_power

        # charge/discharge batteries
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

        return {'current_time': self.current_time, 'commands': charging_stations}
