import events
from strategy import Strategy


class Greedy(Strategy):
    def __init__(self, constants, start_time, **kwargs):
        super().__init__(constants, start_time, **kwargs)
        self.description = "greedy"


    def step(self, event_list=[]):
        super().step(event_list)

        charging_stations = {}
        socs = {}

        for vehicle_id in sorted(self.world_state.vehicles):
            vehicle = self.world_state.vehicles[vehicle_id]
            delta_soc = vehicle.desired_soc - vehicle.battery.soc
            cs_id = vehicle.connected_charging_station
            if delta_soc > 0 and cs_id:
                cs = self.world_state.charging_stations[cs_id]
                # vehicle needs loading
                gc = self.world_state.grid_connectors[cs.parent]
                gc_power_left = gc.cur_max_power - sum(gc.current_loads.values())
                cs_power_left = cs.max_power - charging_stations.get(cs_id, 0)
                max_power =  min(cs_power_left, gc_power_left)

                load_result = vehicle.battery.load(self.interval, max_power)
                avg_power = load_result['avg_power']

                charging_stations[cs_id] = gc.add_load(cs_id, avg_power)

                assert vehicle.battery.soc <= 100
                assert vehicle.battery.soc >= 0, 'SOC of {} is {}'.format(vehicle_id, vehicle.battery.soc)

            socs[vehicle_id] = vehicle.battery.soc

        return {'current_time': self.current_time, 'commands': charging_stations, 'socs': socs}
