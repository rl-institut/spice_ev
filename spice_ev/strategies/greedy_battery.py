from spice_ev.util import clamp_power
from spice_ev.strategy import Strategy


class GreedyBattery(Strategy):
    """ Uncontrolled charging with max power immediately after connecting to charging station.

    Battery behavior: charge with maximum power when no vehicles are charging, otherwise support GC
    """

    def __init__(self, components, start_time, **kwargs):
        super().__init__(components, start_time, **kwargs)
        self.description = "greedy battery"

    def step(self):
        power_used = {}
        avail_bat_power = {}
        for gc_id, gc in self.world_state.grid_connectors.items():
            power_used[gc_id] = gc.get_current_load()
            avail_bat_power[gc_id] = 0
        for battery in self.world_state.batteries.values():
            avail_bat_power[battery.parent] += battery.get_available_power(self.interval)

        # dict to hold charging commands
        charging_stations = {}
        # reset charging station power (nothing charged yet in this timestep)
        for cs in self.world_state.charging_stations.values():
            cs.current_power = 0

        for vehicle_id in sorted(self.world_state.vehicles):
            vehicle = self.world_state.vehicles[vehicle_id]
            cs_id = vehicle.connected_charging_station
            if cs_id is None:
                continue
            cs = self.world_state.charging_stations[cs_id]
            gc_id = cs.parent
            gc = self.world_state.grid_connectors[gc_id]
            power_needed = (
                vehicle.get_delta_soc() * vehicle.battery.capacity /
                vehicle.battery.efficiency * self.ts_per_hour)
            power_available = gc.cur_max_power - power_used[gc_id] + avail_bat_power[gc_id]
            power = clamp_power(min(power_needed, power_available), vehicle, cs)
            avg_power = vehicle.battery.load(self.interval, target_power=power)['avg_power']
            cs.current_power = avg_power
            power_used[gc_id] += avg_power
            avail_bat_power[gc_id] -= min(avg_power, avail_bat_power[gc_id])
            charging_stations[cs_id] = gc.add_load(cs_id, avg_power)

        # all vehicles charged: distribute surplus
        # first supercharge vehicles
        for vehicle_id in sorted(self.world_state.vehicles):
            vehicle = self.world_state.vehicles[vehicle_id]
            cs_id = vehicle.connected_charging_station
            if cs_id is None:
                continue
            gc_id = cs.parent
            gc = self.world_state.grid_connectors[gc_id]
            # add surplus, if any
            power = clamp_power(max(-power_used[gc_id], 0), vehicle, cs)
            avg_power = vehicle.battery.load(self.interval, target_power=power)['avg_power']
            charging_stations[cs_id] = gc.add_load(cs_id, avg_power)

        # update batteries
        for bat_id, battery in self.world_state.batteries.items():
            gc_id = battery.parent
            gc = self.world_state.grid_connectors[gc_id]
            gc_current_load = gc.get_current_load()
            if gc_current_load > 0:
                # GC draws power: use stored energy to support GC
                bat_power = battery.unload(self.interval, target_power=gc_current_load)['avg_power']
                gc.add_load(bat_id, -bat_power)
            else:
                # GC does not draw power or has surplus: charge battery greedy
                power = gc.cur_max_power - gc_current_load
                power = 0 if power < battery.min_charging_power else power
                avg_power = battery.load(self.interval, target_power=power)['avg_power']
                gc.add_load(bat_id, avg_power)

        return {'current_time': self.current_time, 'commands': charging_stations}
