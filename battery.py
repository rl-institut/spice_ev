import copy


class Battery:
    def __init__(self, capacity, loading_curve, soc):
        self.capacity = capacity
        self.loading_curve = copy.deepcopy(loading_curve)
        self.soc = soc


    def load(timedelta, max_charging_power):
        """ Adjust SOC and return average charging power for a given timedelta
        and maximum charging power.
        """

        seconds = timedelta.total_seconds()

        return {'avg_power': 1000}


    def __str__(self):
        return 'Battery {}'.format({ k: str(v) for k, v in vars(self).items() })
