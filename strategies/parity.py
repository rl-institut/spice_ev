import events
from strategy import Strategy


class Parity(Strategy):
    """
    Charging strategy that distributes power evenly among cars.
    """
    def __init__(self, constants, start_time, **kwargs):
        super().__init__(constants, start_time, **kwargs)
        self.description = "parity"


    def step(self, event_list=[]):
        super().step(event_list)

        # charging vehicle at which grid connector?
        vehicle_to_grid = {}
        charging_stations = {}

        # gather all vehicles in need of charge
        for vehicle_id, vehicle in self.world_state.vehicles.items():
            delta_soc = vehicle.desired_soc - vehicle.battery.soc
            cs_id = vehicle.connected_charging_station
            if delta_soc > 0 and cs_id:
                cs = self.world_state.charging_stations[cs_id]
                gc_id = cs.parent
                if gc_id in vehicle_to_grid:
                    vehicle_to_grid[gc_id].append(vehicle_id)
                else:
                    vehicle_to_grid[gc_id] = [vehicle_id]

        # distribute power of each grid connector
        for gc_id, gc in self.world_state.grid_connectors.items():
            gc_power_left = gc.cur_max_power - sum(gc.current_loads.values())
            vehicles = vehicle_to_grid.get(gc_id, [])

            for vehicle_id in vehicles:
                vehicle = self.world_state.vehicles[vehicle_id]
                cs_id = vehicle.connected_charging_station
                cs = self.world_state.charging_stations[cs_id]
                vehicles_at_cs = list(filter(lambda v: self.world_state.vehicles[v].connected_charging_station == cs_id, vehicles))

                # find minimum of distributed power and charging station power
                gc_dist_power = gc_power_left / len(vehicles)
                gc_dist_power = min(gc_dist_power, cs.max_power)
                # CS guaranteed to have one requesting vehicle
                cs_dist_power = gc_dist_power / len(vehicles_at_cs)

                # load battery
                load_result = vehicle.battery.load(self.interval, cs_dist_power)
                avg_power = load_result['avg_power']

                charging_stations[cs_id] = gc.add_load(cs_id, avg_power)

                assert vehicle.battery.soc <= 100
                assert vehicle.battery.soc >= 0, 'SOC of {} is {}'.format(vehicle_id, vehicle.battery.soc)

        socs={vid: v.battery.soc for vid, v in self.world_state.vehicles.items()}

        return {'current_time': self.current_time, 'commands': charging_stations, 'socs': socs}
