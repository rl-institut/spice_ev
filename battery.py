import copy
from math import exp, log

class Battery:
    def __init__(self, capacity, loading_curve, soc):
        self.capacity = capacity
        self.loading_curve = copy.deepcopy(loading_curve)
        self.soc = soc

    def load(self, timedelta, max_charging_power):
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
            x2 = clamped.points[idx_2][0]
            if self.soc >= x2:
                idx_1 += 1
            else:
                break

        power = []

        # compute average power for each linear section
        # update SOC
        # computes for whole time or until SOC is 100% (no next section)
        while hours > EPS and 100.0 - self.soc > EPS: #self.soc < 100.0:
            while x2 - self.soc < EPS: # self.soc >= x2:
                # get next section
                idx_1 += 1
                idx_2 += 1
                x1 = clamped.points[idx_1][0]
                x2 = clamped.points[idx_2][0]

            # compute gradient and offset of linear equation
            y1 = clamped.power_from_soc(x1)
            y2 = clamped.power_from_soc(x2)
            dx = x2 - x1
            dy = y2 - y1

            m = dy / dx
            n = y1 - m * x1
            c = self.capacity

            # find time to breakpoint
            try:
                if m == 0:
                    # simple constant charging
                    t = (x2 - self.soc) * c / (n * 100)
                else:
                    # inverse of exponential function
                    t = log((x2 + n/m) / (self.soc + n/m)) * c/m / 100
            except (ValueError, ZeroDivisionError):
                t = hours

            # what is earlier, breakpoint or interval end?
            t = min(t, hours)

            if m==0:
                # simple case: charging with constant power, regardless of SOC
                new_soc = self.soc + (n/c * t)*100
            else:
                # charge power dependent on SOC
                # inhomogenous differential equation -> exponential function
                new_soc = -n/m + (n/m + self.soc) * exp(m/c * 100 * t)

            # compute energy and power
            energy_delta = (new_soc - self.soc) / 100 * c
            power.append(energy_delta / t)
            self.soc = new_soc
            hours -= t

        # get average power in all segments
        avg_power = sum(power)/len(power) if len(power) else 0
        return {'avg_power': avg_power, 'soc_delta': self.soc - old_soc}


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
            delta_soc = 100.0 * energy_delta / self.capacity
            self.soc += delta_soc

            if self.soc >= 100:
                avg_power -= loading_power * (100 - self.soc) / delta_soc
                self.soc = 100
                break

        avg_power /= seconds
        soc_delta = self.soc - old_soc

        return {'avg_power': avg_power, 'soc_delta': soc_delta}


    def __str__(self):
        return 'Battery {}'.format({ k: str(v) for k, v in vars(self).items() })
