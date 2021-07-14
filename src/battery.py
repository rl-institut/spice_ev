import copy
from math import exp, log


class Battery:
    def __init__(self, capacity, loading_curve, soc, efficiency=0.95):
        self.capacity = capacity
        self.loading_curve = copy.deepcopy(loading_curve)
        self.soc = soc
        self.efficiency = efficiency

    def load(self, timedelta, max_charging_power, target_soc=1):
        """ Adjust SOC and return average charging power for a given timedelta
        and maximum charging power.
        """

        # epsilon for floating point comparison
        EPS = 1e-5

        # get interval in hours
        hours = timedelta.total_seconds() / 3600.0

        # get loading curve clamped to maximum value
        clamped = self.loading_curve.clamped(max_charging_power)

        avg_power = 0
        old_soc = self.soc

        # find current region in loading curve
        idx_1 = 0
        while idx_1 < len(clamped.points) - 1:
            idx_2 = idx_1 + 1
            x1 = clamped.points[idx_1][0]
            x2 = min(target_soc, clamped.points[idx_2][0])
            if self.soc >= x2:
                idx_1 += 1
            else:
                break

        power = []

        # compute average power for each linear section
        # update SOC
        # computes for whole time or until target is reached
        while hours > EPS and target_soc - self.soc > EPS:  # self.soc < target:
            while x2 - self.soc < EPS:  # self.soc >= x2:
                # get next section
                idx_1 += 1
                idx_2 += 1
                x1 = clamped.points[idx_1][0]
                x2 = min(target_soc, clamped.points[idx_2][0])

            # compute gradient and offset of linear equation
            y1 = clamped.power_from_soc(x1) * self.efficiency
            y2 = clamped.power_from_soc(x2) * self.efficiency
            dx = x2 - x1
            dy = y2 - y1

            m = dy / dx
            n = y1 - m * x1
            c = self.capacity

            # find time to breakpoint
            try:
                if m == 0:
                    # simple constant charging
                    t = (x2 - self.soc) * c / n
                else:
                    # inverse of exponential function
                    t = log((x2 + n/m) / (self.soc + n/m)) * c/m
            except (ValueError, ZeroDivisionError):
                t = hours

            # what is earlier, breakpoint or interval end?
            t = min(t, hours)

            if m == 0:
                # simple case: charging with constant power, regardless of SOC
                new_soc = self.soc + (n/c * t)
            else:
                # charge power dependent on SOC
                # inhomogenous differential equation -> exponential function
                new_soc = -n/m + (n/m + self.soc) * exp(m/c * t)

            # compute energy and power
            energy_delta = (new_soc - self.soc) * c
            power.append(energy_delta / t / self.efficiency)
            self.soc = new_soc
            hours -= t

        # get average power in all segments
        avg_power = sum(power)/len(power) if len(power) else 0
        return {'avg_power': avg_power, 'soc_delta': self.soc - old_soc}

    def unload(self, timedelta, max_power=None, target_soc=0):
        # unload battery with constant power over timedelta
        # can use specific power - default: loading curve max power
        # can set target SOC (don't discharge below this threshold)
        if max_power is None:
            avg_power = self.loading_curve.max_power
        else:
            avg_power = min(max(max_power, 0), self.loading_curve.max_power)

        delta_soc = max(self.soc - target_soc, 0)
        hours = timedelta.total_seconds() / 3600.0

        # how long until target SOC reached?
        t = delta_soc * self.capacity / avg_power if avg_power > 0 else hours
        # within time?
        t = min(t, hours)
        # power = energy / t
        # energy = soc * c

        # discharge battery with average power over time
        delta_energy = avg_power * t
        delta_soc = delta_energy / self.capacity
        self.soc -= delta_soc
        avg_power = delta_energy / t * self.efficiency if t > 0 else 0
        return {'avg_power': avg_power, 'soc_delta': delta_soc}

    def load_iterative(self, timedelta, max_charging_power):
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
            delta_soc = energy_delta / self.capacity * self.efficiency
            self.soc += delta_soc

            if self.soc >= 1:
                avg_power -= (loading_power * (1 - self.soc) / delta_soc / self.efficiency)
                self.soc = 1
                break

        avg_power /= seconds
        soc_delta = self.soc - old_soc

        return {'avg_power': avg_power, 'soc_delta': soc_delta}

    def get_available_power(self, timedelta):
        # returns maximum available power for timedelta duration
        old_soc = self.soc
        power = self.unload(timedelta)['avg_power']
        self.soc = old_soc
        return power

    def __str__(self):
        return 'Battery {}'.format({k: str(v) for k, v in vars(self).items()})
