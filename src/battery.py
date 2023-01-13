import copy
from math import exp, log

from src.loading_curve import LoadingCurve


class Battery():
    """Battery class"""
    def __init__(self, capacity, loading_curve, soc,
                 efficiency=0.95, unloading_curve=None, loss_rate=None):
        """ Initialize the battery.

        :param capacity: capacity of the battery in kWh
        :type capacity: numerical
        :param loading_curve: loading curve of the battery
        :type loading_curve: src.loading_curve.LoadingCurve
        :param soc: soc of the battery
        :type soc: float
        :param efficiency: efficiency of the battery
        :type efficiency: float
        :param unloading_curve: unloading curve of the battery.
            Defaults to None for backwards-compatability (discharge with maximum
            power of loading curve)
        :type unloading_curve: src.loading_curve.LoadingCurve
        :param loss_rate: adjusted loss rate per timestep. Can have keys
            *relative* (percent in relation to current charge),
            *fixed_relative* (percent in relation to capacity) and
            *fixed_absolute* (energy in kWh independent of capacity)
        :type loss_rate: dict
        """
        # epsilon for floating point comparison
        self.EPS = 1e-5
        self.capacity = capacity
        self.loading_curve = copy.deepcopy(loading_curve)
        self.soc = soc
        self.efficiency = efficiency
        self.loss_rate = loss_rate
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
        if self.soc - target_soc > self.EPS:
            # target SoC already reached: skip loading
            return {'avg_power': 0, 'soc_delta':  0}

        old_soc = self.soc
        # get loading curve clamped to maximum value
        # adjust charging curve to reflect power that reaches the battery
        # after losses due to efficiency
        clamped = self.loading_curve.clamped(max_charging_power, post_scale=self.efficiency)
        # get average power (energy over complete timedelta)
        avg_power = self._adjust_soc(charging_curve=clamped,
                                     target_soc=target_soc,
                                     timedelta=timedelta)
        # get power that needs to be supplied to the battery by the charging device
        avg_power /= self.efficiency

        return {'avg_power': avg_power, 'soc_delta': self.soc - old_soc}

    def unload(self, timedelta, max_power=None, target_soc=None, target_power=None):
        """
        Discharge battery.

        Adjust SOC and return average power provided for a given timedelta and
        a maximum of power that can be handled by connected device.
        A target SoC or target output power may be given, but not both.
        If neither is provided, the battery just discharges.

        :param timedelta: time period in which battery can be discharged
        :type timedelta: timedelta
        :param max_power: maximum power connected device can receive
        :type max_power: numeric
        :param target_soc: desired soc
        :type target_soc: numeric
        :param target_power: desired output power
        :type target_power: numeric
        :return: average power and soc_delta
        :rtype: dict
        """
        if max_power is None:
            max_power = self.unloading_curve.max_power

        if target_soc is None:
            if target_power is None:
                # nothing set: just unload
                target_soc = 0
            else:
                # target power given -> compute energy difference and delta soc
                total_time = timedelta.total_seconds() / 3600
                energy_delta = target_power / self.efficiency * total_time
                soc_delta = energy_delta / self.capacity
                target_soc = self.soc - soc_delta
        else:
            # target soc given: target power must not be set
            assert target_power is None, "Unload battery: choose either target power or SoC"

        if target_soc - self.soc > self.EPS:
            # target SoC already reached: skip unloading
            return {'avg_power': 0, 'soc_delta':  0}
        if max_power is None:
            max_power = self.unloading_curve.max_power

        old_soc = self.soc
        # get loading curve clamped to maximum value
        # adjust loading curve by efficiency factor to reflect power
        # released by the battery as opposed to power provided to user
        clamped = self.unloading_curve.clamped(max_power, post_scale=1/self.efficiency)
        # get average power (energy over complete timedelta)
        avg_power = self._adjust_soc(charging_curve=clamped,
                                     target_soc=target_soc,
                                     timedelta=timedelta)

        # get power supplied to the connected device/vehicle after loss due to efficiency
        avg_power *= self.efficiency

        return {'avg_power': avg_power, 'soc_delta':  old_soc - self.soc}

    def load_iterative(self, timedelta, max_charging_power):
        """Adjust SOC and return average charging power for a given timedelta
        and maximum charging power.

        :param timedelta: time period in which battery can be loaded
        :type timedelta: timedelta
        :param max_charging_power: maximum charging power
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

    def _adjust_soc(self, timedelta, charging_curve, target_soc):
        """ Helper function that loads or unloads battery to a given target SOC
            keeping track of the duration and stopping the process early if a time limit is reached.

        :param timedelta: Maximum amount of time available for (dis)charge process.
        :type timedelta: datetime.timedelta
        :param charging_curve: The charging curve that relates the SOC to (dis)charge power.
            This charging curve reflects the exact amount of power the battery releases/receives.
            The curve must already be clamped and scaled to account for losses due to efficiency and
            limitations of connected devices.
        :type charging_curve: src.loading_curve.LoadingCurve
        :param target_soc: SOC to (dis)charge to.
        :type target_soc: numeric
        :return: Average power released/received across entire timedelta.
        :rtype: numeric
        """
        # get interval in hours
        total_time = timedelta.total_seconds() / 3600.0
        # hours: available time for charging, initially complete timedelta
        remaining_hours = total_time

        # for certain steps in the process below it matters whether
        # the battery charges or discharges
        discharge = target_soc < self.soc

        # find current region in loading curve
        # the boundary soc is either the target soc or the soc at which the current
        # linear section of the (dis)charging curve ends either of which is closer to current soc.
        # (Note: Ending is a relative concent depending on whether we charge or discharge. During
        # charging we are looking for the section boundary larger than the current soc, while
        # for discharging we look for the smaller counterpart)
        idx_1, idx_2 = charging_curve.get_section_boundary(self.soc)
        if discharge:
            boundary_idx = idx_1
            boundary_soc = max(target_soc, charging_curve.points[idx_1][0])
        else:
            boundary_idx = idx_2
            boundary_soc = min(target_soc, charging_curve.points[idx_2][0])

        # collect energy flowing in or out of battery
        energies = []

        # sign is -1 when discharging, +1 when charging
        sign = (-discharge << 1) + 1

        # compute average power for each linear section
        # update SOC
        # computes for whole time or until target is reached
        while remaining_hours > self.EPS and sign * (target_soc - self.soc) > 0:
            # target soc not yet reached
            # charging: self.soc < target; discharging: self.soc > target
            while sign * (boundary_soc - self.soc) <= 0:
                # soc outside current boundary, get next section
                # charging: self.soc >= boundary_soc; discharging: self.soc <= boudary_soc
                boundary_idx += sign
                if discharge:
                    if boundary_idx >= 0:
                        # current soc in domain of charging curve
                        boundary_soc = max(target_soc, charging_curve.points[boundary_idx][0])
                    else:
                        # current soc < 0 and target soc < current soc
                        boundary_soc = target_soc
                else:
                    boundary_soc = min(target_soc, charging_curve.points[boundary_idx][0])

            x1 = self.soc
            x2 = boundary_soc

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
                t = sign * remaining_hours

            # what is earlier, breakpoint or interval end?
            # keep track of sign(t) as it encodes whether we charge or discharge
            t = ((t > 0) - (t < 0)) * min(abs(t), remaining_hours)

            if abs(m) < self.EPS:
                # simple case: charging with constant power, regardless of SOC
                new_soc = self.soc + (n/c * t)
            else:
                # charge power dependent on SOC
                # inhomogenous differential equation -> exponential function
                new_soc = -n/m + (n/m + self.soc) * exp(m/c * t)

            if discharge:
                assert new_soc <= self.soc, f"Discharge: {new_soc} should be less than {self.soc}"
            else:
                assert new_soc >= self.soc, f"Charge: {new_soc} should be greater than {self.soc}"

            energy_delta = abs(new_soc - self.soc) * c
            self.soc = new_soc
            remaining_hours -= abs(t)
            # remember amount of energy loaded into battery
            energies.append(energy_delta)

        try:
            avg_power = sum(energies) / total_time
        except ZeroDivisionError:
            avg_power = 0

        return avg_power

    def __str__(self):
        return 'Battery {}'.format({k: str(v) for k, v in vars(self).items()})
