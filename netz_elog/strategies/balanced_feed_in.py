import datetime

from netz_elog import events, util
from netz_elog.strategy import Strategy


class BalancedFeedIn(Strategy):
    """
    Charging strategy that calculates the minimum charging power to arrive at the
    desired SOC during the estimated parking time for each vehicle.
    """
    def __init__(self, constants, start_time, **kwargs):
        # defaults
        self.EPS = 1e-5
        self.ITERATIONS = 10

        super().__init__(constants, start_time, **kwargs)
        self.description = "balanced (feed-in)"


    def step(self, event_list=[]):
        super().step(event_list)

        socs = {}
        charging_stations = {}

        # keep track of available power and number of connected vehicles
        gc_info = {gc_id: {
            "vehicles": [],
            "batteries": [],
            "power": max(gc.get_external_load(), -gc.cur_max_power)
        } for gc_id, gc in self.world_state.grid_connectors.items()}

        for vehicle_id in sorted(self.world_state.vehicles):
            # get vehicle
            vehicle = self.world_state.vehicles[vehicle_id]
            cs_id = vehicle.connected_charging_station
            if cs_id is None:
                # not connected
                continue
            # get connected charging station
            cs = self.world_state.charging_stations[cs_id]
            # vehicle belongs to this GC
            gc_info[cs.parent]["vehicles"].append(vehicle_id)

        for bat_id, bat in self.world_state.batteries.items():
            gc_info[bat.parent]["batteries"].append(bat_id)

        # charging
        for gc_id, info in gc_info.items():
            gc = self.world_state.grid_connectors[gc_id]
            for vehicle_id in info["vehicles"]:
                vehicle = self.world_state.vehicles[vehicle_id]
                cs_id = vehicle.connected_charging_station
                cs = self.world_state.charging_stations[cs_id]

                # charge from feed-in
                power = -min(info["power"], 0) / len(info["vehicles"])
                avg_power = vehicle.battery.load(self.interval, power)['avg_power']
                charging_stations[cs_id] = gc.add_load(cs_id, avg_power)

                # compute leftover charge need
                delta_soc = vehicle.get_delta_soc()
                if delta_soc > self.EPS:
                    # vehicle needs charging
                    min_power = vehicle.vehicle_type.min_charging_power
                    max_power = vehicle.vehicle_type.charging_curve.max_power
                    # time until departure
                    dt = vehicle.estimated_time_of_departure - self.current_time - datetime.timedelta(hours=1)
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
                        elif charged_soc - delta_soc > self.EPS: #charged_soc > delta_soc:
                            # power too much
                            safe = True
                            max_power = power
                        else:
                            # power exactly right
                            break

                    cs.current_power = power

                gc_power_left = max(gc.cur_max_power - gc.get_external_load(), 0)
                old_soc = vehicle.battery.soc
                # load with power
                avg_power = vehicle.battery.load(self.interval, power)['avg_power']
                if avg_power > gc_power_left:
                    # GC at limit: try again with less power
                    vehicle.battery.soc = old_soc
                    avg_power = vehicle.battery.load(self.interval, gc_power_left)['avg_power']

                charging_stations[cs_id] = gc.add_load(cs_id, avg_power)

                # can active charging station bear minimum load?
                assert cs.max_power >= cs.current_power - self.EPS, "{} - {} over maximum load ({} > {})".format(self.current_time, cs_id, cs.current_power, cs.max_power)
                # can grid connector bear load?
                assert  gc.cur_max_power >= gc.get_external_load() - self.EPS, "{} - {} over maximum load ({} > {})".format(self.current_time, cs.parent, gc_current_power, gc.cur_max_power)

                # take note of final vehicle SOC
                socs[vehicle_id] = vehicle.battery.soc

            gc_price = util.get_cost(1, gc.cost)
            gc_power = gc.get_external_load()
            for bat_id in info["batteries"]:
                bat = self.world_state.batteries[bat_id]
                if gc_price <= 0:
                    # free energy: load with max power
                    bat_power = bat.load(self.interval, bat.loading_curve.max_power)['avg_power']
                    gc.add_load(bat_id, bat_power)
                else:
                    # price above zero
                    ind_power = gc_power / len(info["batteries"])
                    if gc_power >= 0:
                        # GC draws from supply: support by unloading battery
                        bat_power = bat.unload(self.interval, ind_power)['avg_power']
                        gc.add_load(bat_id, -bat_power)
                    else:
                        # GC has surplus power: store in battery
                        bat_power = bat.load(self.interval, -ind_power)['avg_power']
                        gc.add_load(bat_id, bat_power)

        return {'current_time': self.current_time, 'commands': charging_stations, 'socs': socs}
