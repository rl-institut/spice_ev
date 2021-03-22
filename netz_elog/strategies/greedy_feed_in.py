from netz_elog import events
from netz_elog.strategy import Strategy


class GreedyFeedIn(Strategy):
    def __init__(self, constants, start_time, **kwargs):
        self.CONCURRENCY=1.0
        super().__init__(constants, start_time, **kwargs)
        self.description = "greedy (feed-in)"


    def step(self, event_list=[]):
        super().step(event_list)

        charging_stations = {}
        socs = {}

        for vehicle_id in sorted(self.world_state.vehicles):
            vehicle = self.world_state.vehicles[vehicle_id]
            cs_id = vehicle.connected_charging_station
            if not cs_id:
                continue
            cs = self.world_state.charging_stations[cs_id]
            gc = self.world_state.grid_connectors[cs.parent]

            if gc.get_external_load() < 0 or vehicle.get_delta_soc() > 0:

                # feed-in surplus or vehicle needs loading: charge with max power
                # compute power left for vehicle's GC

                gc_power_left = gc.cur_max_power - gc.get_external_load()

                cs_power_left = (self.CONCURRENCY * cs.max_power) - charging_stations.get(cs_id, 0)

                max_power =  min(cs_power_left, gc_power_left)

                load_result = vehicle.battery.load(self.interval, max_power)
                avg_power = load_result['avg_power']

                charging_stations[cs_id] = gc.add_load(cs_id, avg_power)

                assert vehicle.battery.soc <= 100
                assert vehicle.battery.soc >= 0, 'SOC of {} is {}'.format(vehicle_id, vehicle.battery.soc)

            socs[vehicle_id] = vehicle.battery.soc

        return {'current_time': self.current_time, 'commands': charging_stations, 'socs': socs}
