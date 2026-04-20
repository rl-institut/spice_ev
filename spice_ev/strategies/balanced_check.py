from spice_ev.util import clamp_power
from spice_ev.strategy import Strategy


class BalancedCheck(Strategy):
    """ Charging with minimum required power to reach desired SoC during estimated parking time.
    In contrast to default balanced strategy, simulates standing time to check validity
    (might differ because of battery curve etc).
    """
    def __init__(self, components, start_time, **kwargs):
        # defaults
        super().__init__(components, start_time, **kwargs)
        self.description = "balanced with check"
        # memoize vehicle charging power
        self.vehicle_power = {v_id: None for v_id in self.world_state.vehicles.keys()}

    def step(self):
        """ Calculates charging power in each timestep.

        :return: current time and commands of the charging stations
        :rtype: dict
        """
        charging_stations = {}
        for v_id, vehicle in sorted(self.world_state.vehicles.items()):
            cs_id = vehicle.connected_charging_station
            if cs_id is None:
                # not connected
                self.vehicle_power[v_id] = None
                continue
            delta_soc = vehicle.get_delta_soc()
            if delta_soc < self.EPS:
                # vehicle charged enough
                continue

            # get connected charging station and GC info
            cs = self.world_state.charging_stations[cs_id]
            cs.current_power = 0
            gc_id = cs.parent
            gc = self.world_state.grid_connectors[gc_id]
            gc_power_left = gc.cur_max_power - gc.get_current_load()

            power = self.vehicle_power.get(v_id)
            min_power = None
            max_power = None
            old_soc = vehicle.battery.soc
            if power is not None:
                if len(power) > 0:
                    # power already computed
                    power = power.pop(0)
                    if power > gc_power_left:
                        # needed power exceeds GC capacity: recalculate
                        power = None
                        self.vehicle_power[v_id] = None
                    else:
                        # try to charge
                        ap = vehicle.battery.load(self.interval, target_power=power)['avg_power']
                        if ap + self.EPS < power:
                            # power not sufficient: recalculate
                            power = None
                            self.vehicle_power[v_id] = None
                        vehicle.battery.soc = old_soc
                else:
                    # not charged enough, but after estimated time of departure:
                    # charge greedy
                    power = clamp_power(gc_power_left, vehicle, cs)

            if power is None:
                # (re)calculate power
                energy_needed = delta_soc * vehicle.battery.capacity / vehicle.battery.efficiency
                # time until departure
                dt = vehicle.estimated_time_of_departure - self.current_time
                timesteps = -(dt // -self.interval)
                timesteps = max(timesteps, 1)
                min_power = energy_needed * self.ts_per_hour / timesteps
                min_power = clamp_power(min_power, vehicle, cs)

                # first timestep: check GC power
                power = min(min_power, gc_power_left)
                power = clamp_power(power, vehicle, cs)
                vehicle.battery.load(self.interval, target_power=power)['avg_power']
                powers = list()
                p = clamp_power(min_power, vehicle, cs)
                for _ in range(1, timesteps):
                    ap = vehicle.battery.load(self.interval, target_power=p)['avg_power']
                    powers.append(ap)
                if vehicle.get_delta_soc() < self.EPS:
                    # success: save future powers
                    self.vehicle_power[v_id] = powers
                else:
                    # not charged enough: try more power
                    power = None
                vehicle.battery.soc = old_soc

            if power is None:
                # try with maximum power
                max_power = clamp_power(gc.cur_max_power, vehicle, cs)
                # first timestep: check GC power
                power = min(max_power, gc_power_left)
                power = clamp_power(power, vehicle, cs)
                vehicle.battery.load(self.interval, target_power=power)['avg_power']
                powers = list()
                for _ in range(1, timesteps):
                    # simulate with max_power (already clamped)
                    ap = vehicle.battery.load(self.interval, target_power=max_power)['avg_power']
                    powers.append(ap)
                if vehicle.get_delta_soc() < self.EPS:
                    # with max power it is possible to fully charge vehicle:
                    # target power must be between min_power and max_power
                    power = None
                else:
                    # even with full power vehicle can't be charged enough:
                    # use full power
                    self.vehicle_power[v_id] = powers
                vehicle.battery.soc = old_soc

            if power is None:
                # seek optimal power between min_power and max_power
                safe = False
                while min_power + self.EPS < max_power or not safe:
                    avg_power = (min_power + max_power) / 2
                    power = min(avg_power, gc_power_left)
                    power = clamp_power(power, vehicle, cs)
                    vehicle.battery.load(self.interval, target_power=power)['avg_power']
                    powers = list()
                    p = clamp_power(avg_power, vehicle, cs)
                    for _ in range(1, timesteps):
                        ap = vehicle.battery.load(self.interval, target_power=p)['avg_power']
                        powers.append(ap)
                    safe = vehicle.get_delta_soc() < self.EPS
                    if safe:
                        # vehicle charged enough: lower max power
                        max_power = avg_power
                    else:
                        # vehicle not charged enough: increase min power
                        min_power = avg_power
                    vehicle.battery.soc = old_soc
                self.vehicle_power[v_id] = powers

            # charge with calculated power
            # this may be less than clamped power, because of battery charging curve
            avg_power = vehicle.battery.load(self.interval, target_power=power)['avg_power']
            charging_stations[cs_id] = gc.add_load(cs_id, avg_power)
            cs.current_power += avg_power

        # all vehicles charged
        # charging_stations.update(self.distribute_surplus_power())
        # self.update_batteries()

        return {'current_time': self.current_time, 'commands': charging_stations}
