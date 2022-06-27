import copy
from math import exp, log

from src.loading_curve import LoadingCurve


class Battery:
    """Battery class"""
    def __init__(self, capacity, loading_curve, soc, efficiency=0.95, unloading_curve=None):
        """ Initializing the battery

        :param capacity: capacity of the battery
        :type capacity: int/float
        :param loading_curve: loading curve of the battery
        :type loading_curve: dict
        :param soc: soc of the battery
        :type soc: float
        :param efficiency: efficiency of the battery
        :type efficiency: float
        """
        # epsilon for floating point comparison
        self.EPS = 1e-5
        self.capacity = capacity
        self.loading_curve = copy.deepcopy(loading_curve)
        self.soc = soc
        self.efficiency = efficiency
        if unloading_curve is None:
            self.unloading_curve = LoadingCurve([[0, self.loading_curve.max_power],
                                                 [1, self.loading_curve.max_power]])
        else:
            self.unloading_curve = copy.deepcopy(unloading_curve)

    def load(self, timedelta, max_charging_power, target_soc=1):
        """ Adjust SOC and return average charging power for a given timedelta
        and maximum charging power.

        :param timedelta: time period in which battery can charge
        :type timedelta: timedelta
        :param max_charging_power: maximum charging power
        :type max_charging_power: numeric
        :param target_soc: desired soc
        :type target_soc: numeric
        :return: average power and soc_delta
        :rtype: dict
        """

        # get interval in hours
        total_time = timedelta.total_seconds() / 3600.0
        # hours: available time for charging, initially complete timedelta
        remaining_hours = total_time

        # get loading curve clamped to maximum value
        # adjust charging curve to reflect power that reaches the battery
        # after losses due to efficieny
        clamped = self.loading_curve.clamped(max_charging_power).scale(self.efficiency)

        avg_power = 0
        old_soc = self.soc

        # find current region in loading curve
        _, idx_2 = clamped.get_linear_section(self.soc)
        x2 = min(target_soc, clamped.points[idx_2][0])

        energies = []

        # compute average power for each linear section
        # update SOC
        # computes for whole time or until target is reached
        # TODO: move loops to other function
        while remaining_hours > self.EPS and target_soc - self.soc > self.EPS:  # self.soc < target:
            while x2 - self.soc < self.EPS:  # self.soc >= x2:
                # get next section
                idx_2 += 1
                x2 = min(target_soc, clamped.points[idx_2][0])

            energy_delta, remaining_hours = self._adjust_soc(charging_curve=clamped,
                                                             target_soc=x2,
                                                             time_limit=remaining_hours)
            # remember amount of energy loaded into battery
            energies.append(energy_delta)

        # get average power (energy over complete timedelta)
        # supplied to the battery before loss due to efficieny
        try:
            avg_power = (sum(energies) / self.efficiency) / total_time
        except ZeroDivisionError:
            avg_power = 0

        return {'avg_power': avg_power, 'soc_delta': self.soc - old_soc}

    def unload(self, timedelta, max_power=None, target_soc=0):
        """ Adjust SOC and return average power provided for a given timedelta and
        a maximum of power that can be handled by connected device.

        :param timedelta: time period in which battery can be discharged
        :type timedelta: timedelta
        :param max_power: maximum power connected device can receive
        :type max_power: numeric
        :param target_soc: desired soc
        :type target_soc: numeric
        :return: average power and soc_delta
        :rtype: dict

        notes:
        * can use specific power - default: loading curve max power
        * can set target SOC (don't discharge below this threshold)
        """

        if max_power is None:
            max_power = self.unloading_curve.max_power

        # get interval in hours
        total_time = timedelta.total_seconds() / 3600.0
        # hours: available time for charging, initially complete timedelta
        remaining_hours = total_time

        # get loading curve clamped to maximum value
        # adjust loading curve by efficiency factor to reflect power
        # flowing out of the battery as opposed to power provided by the battery to user
        clamped = self.unloading_curve.clamped(max_power).scale(1/self.efficiency)

        avg_power = 0
        old_soc = self.soc

        # find initial linear section
        idx_2, _ = clamped.get_linear_section(self.soc)
        x2 = max(target_soc, clamped.points[idx_2][0])

        energies = []

        # compute average power for each linear section
        # update SOC
        # computes for whole time or until target is reached
        while remaining_hours > self.EPS and self.soc - target_soc > self.EPS:  # self.soc > target:
            while self.soc - x2 < self.EPS:  # self.soc <= x2:
                # get next section
                idx_2 -= 1
                x2 = max(target_soc, clamped.points[idx_2][0])

            energy_delta, remaining_hours = self._adjust_soc(charging_curve=clamped,
                                                             target_soc=x2,
                                                             time_limit=remaining_hours)
            # remember amount of energy provided
            energies.append(energy_delta)

        # get average power (energy over complete timedelta)
        # supplied by the system to the connected device/vehicle after loss due to efficiency
        try:
            avg_power = (sum(energies) * self.efficiency) / total_time
        except ZeroDivisionError:
            avg_power = 0

        return {'avg_power': avg_power, 'soc_delta':  old_soc - self.soc}

    def load_iterative(self, timedelta, max_charging_power):
        """Adjust SOC and return average charging power for a given timedelta
        and maximum charging power.

        :param timedelta: time period in which battery can be loaded
        :type timedelta: timedelta
        :param max_power: maximum charging power
        :type max_charging_power: numeric
        :return: average power and soc_delta
        :rtype: dict
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
        """Returns maximum available power for timedelta duration.

        :param timedelta: time period
        :type timedelta: timedelta
        :return: power
        :rtype: numeric
        """
        old_soc = self.soc
        power = self.unload(timedelta)['avg_power']
        self.soc = old_soc
        return power

    def _adjust_soc(self, charging_curve, target_soc, time_limit):
        """ Helper function that loads or unloads battery to a given target SOC keeping track of
            the duration and stopping the process early if a time limit is reached.

        :param charging_curve: _description_
        :type charging_curve: _type_
        :param initial_soc: _description_
        :type initial_soc: _type_
        :param target_soc: _description_
        :type target_soc: _type_
        :param time_limit: _description_
        :type time_limit: _type_
        :return: _description_
        :rtype: _type_
        """        # compute gradient and offset of linear equation

        if abs(self.soc - target_soc) < self.EPS:
            # if target soc has already been reached, do not (dis)charge
            energy_delta = 0
            return energy_delta, time_limit

        x1 = self.soc
        x2 = target_soc
        hours = time_limit

        y1 = charging_curve.power_from_soc(x1)
        y2 = charging_curve.power_from_soc(x2)
        dx = x2 - x1
        dy = y2 - y1

        m = dy / dx
        n = y1 - m * x1
        c = self.capacity

        # find time to breakpoint
        try:
            if abs(m) < self.EPS:
                # simple constant charging
                t = (x2 - self.soc) * c / n
            else:
                # inverse of exponential function
                t = log((x2 + n/m) / (self.soc + n/m)) * c/m
        except (ValueError, ZeroDivisionError):
            t = hours

        # what is earlier, breakpoint or interval end?
        # keep track of sign(t) as it encodes whether we charge or discharge
        t = ((t >= 0) - (t < 0)) * min(abs(t), hours)

        if abs(m) < self.EPS:
            # simple case: charging with constant power, regardless of SOC
            new_soc = self.soc + (n/c * t)
        else:
            # charge power dependent on SOC
            # inhomogenous differential equation -> exponential function
            new_soc = -n/m + (n/m + self.soc) * exp(m/c * t)

        energy_delta = abs(new_soc - self.soc) * c
        self.soc = new_soc
        hours -= abs(t)

        return energy_delta, hours

    def __str__(self):
        return 'Battery {}'.format({k: str(v) for k, v in vars(self).items()})
