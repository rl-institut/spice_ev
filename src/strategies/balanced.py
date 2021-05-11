import datetime

from src import events
from src.strategy import Strategy


class Balanced(Strategy):
    """
    Charging strategy that calculates the minimum charging power to arrive at the
    desired SOC during the estimated parking time for each vehicle.
    """
    def __init__(self, constants, start_time, **kwargs):
        # defaults
        self.EPS = 1e-5
        self.ITERATIONS = 10

        super().__init__(constants, start_time, **kwargs)
        self.description = "balanced"


    def step(self, event_list=[]):
        super().step(event_list)

        charging_stations = {}

        for vehicle_id in sorted(self.world_state.vehicles):
            # get vehicle
            vehicle = self.world_state.vehicles[vehicle_id]
            delta_soc = vehicle.get_delta_soc()
            cs_id = vehicle.connected_charging_station
            if cs_id is None:
                # not connected
                continue
            # get connected charging station
            cs = self.world_state.charging_stations[cs_id]

            if delta_soc > self.EPS:
                # vehicle needs charging
                if cs.current_power == 0:
                    # not precomputed
                    min_power = vehicle.vehicle_type.min_charging_power
                    max_power = min(vehicle.vehicle_type.charging_curve.max_power, cs.max_power)
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
                        # get new power value
                        power = (max_power + min_power) / 2
                        # load whole time with same power
                        charged_soc = vehicle.battery.load(dt, power)["soc_delta"]
                        # reset SOC
                        vehicle.battery.soc = old_soc

                        if delta_soc - charged_soc > self.EPS: #charged_soc < delta_soc
                            # power not enough
                            safe = False
                            min_power = power
                        else: #charged_soc >= delta_soc:
                            # power too much or just right (may be possible with less power)
                            safe = True
                            max_power = power

                    # safe power for next time
                    cs.current_power = power

                else:
                    # power precomputed: use again
                    power = cs.current_power


                gc = self.world_state.grid_connectors[cs.parent]
                gc_power_left = max(0, gc.cur_max_power - sum(gc.current_loads.values()))
                if power < cs.min_power or power < vehicle.vehicle_type.min_charging_power:
                    # power too low -> don't charge
                    cs.current_power = 0
                    continue

                old_soc = vehicle.battery.soc
                # load with power
                avg_power = vehicle.battery.load(self.interval, power)['avg_power']
                if avg_power > gc_power_left:
                    # GC at limit: try again with less power
                    vehicle.battery.soc = old_soc
                    avg_power = vehicle.battery.load(self.interval, gc_power_left)['avg_power']
                    # compute new plan next time
                    cs.current_power = 0

                assert vehicle.battery.soc <= 100
                assert vehicle.battery.soc >= 0, 'SOC of {} is {}'.format(vehicle_id, vehicle.battery.soc)

                charging_stations[cs_id] = gc.add_load(cs_id, avg_power)

        # update charging stations
        for cs_id, cs in self.world_state.charging_stations.items():
            if cs_id not in charging_stations:
                # CS currently inactive
                cs.current_power = 0
            else:
                # can active charging station bear minimum load?
                assert cs.max_power >= cs.current_power - self.EPS, "{} - {} over maximum load ({} > {})".format(self.current_time, cs_id, cs.current_power, cs.max_power)
                # can grid connector bear load?
                gc = self.world_state.grid_connectors[cs.parent]
                gc_current_power = sum(gc.current_loads.values())
                assert  gc.cur_max_power >= gc_current_power - self.EPS, "{} - {} over maximum load ({} > {})".format(self.current_time, cs.parent, gc_current_power, gc.cur_max_power)

        socs={vid: v.battery.soc for vid, v in self.world_state.vehicles.items()}

        return {'current_time': self.current_time, 'commands': charging_stations, 'socs': socs}
