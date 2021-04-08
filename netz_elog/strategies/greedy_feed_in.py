from netz_elog import events, util
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

                max_power =  max(min(cs_power_left, gc_power_left), 0)

                load_result = vehicle.battery.load(self.interval, max_power)
                avg_power = load_result['avg_power']

                charging_stations[cs_id] = gc.add_load(cs_id, avg_power)

                assert vehicle.battery.soc <= 100
                assert vehicle.battery.soc >= 0, 'SOC of {} is {}'.format(vehicle_id, vehicle.battery.soc)

            socs[vehicle_id] = vehicle.battery.soc

        # stationary batteries
        for bat_id, bat in self.world_state.batteries.items():
            gc = self.world_state.grid_connectors[bat.parent]
            gc_power = gc.get_external_load()
            gc_price = util.get_cost(1, gc.cost)
            if gc_price <= 0:
                # free energy: load with max power
                bat_power = bat.load(self.interval, bat.loading_curve.max_power)['avg_power']
                gc.add_load(bat_id, bat_power)
            else:
                # price above zero
                if gc_power >= 0:
                    # GC draws from supply: support by unloading battery
                    bat_power = bat.unload(self.interval, gc_power)['avg_power']
                    gc.add_load(bat_id, -bat_power)
                else:
                    # GC has surplus power: store in battery
                    bat_power = bat.load(self.interval, -gc_power)['avg_power']
                    gc.add_load(bat_id, bat_power)

        return {'current_time': self.current_time, 'commands': charging_stations, 'socs': socs}
