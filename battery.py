import copy


class Battery:
    def __init__(self, capacity, loading_curve, soc):
        self.capacity = capacity
        self.loading_curve = copy.deepcopy(loading_curve)
        self.soc = soc


    def energy_to_soc(self, energy):
        return 100 * energy / self.capacity


    def load(self, timedelta, max_charging_power):
        """ Adjust SOC and return average charging power for a given timedelta
        and maximum charging power.
        """

        seconds = timedelta.total_seconds()

        clamped = self.loading_curve.clamped(max_charging_power)

        avg_power = 0
        old_soc = self.soc

        # iterative solution
        for _ in range(round(seconds)):
            loading_power = clamped.power_from_soc(self.soc)
            avg_power += loading_power
            energy_delta = loading_power / 3600
            self.soc += self.energy_to_soc(energy_delta)

        avg_power /= seconds
        soc_delta = self.soc - old_soc

        return {'avg_power': avg_power, 'soc_delta': soc_delta}


    def __str__(self):
        return 'Battery {}'.format({ k: str(v) for k, v in vars(self).items() })
