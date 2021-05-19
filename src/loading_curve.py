
class LoadingCurve:
    """ The loading curve is described by a 2D graph with given known points.
    Values between known points are computed by linear interpolation.

    x-axis: SOC in percent, 0 - 100%
    y-axis: Possible charging power in kW 0 - ∞ kW
    """
    def __init__(self, points):
        self.points = []
        self.max_power = 0
        for p in sorted(points, key=lambda a: a[0]):
            assert len(p) == 2, 'len of {} is {}'.format(p, len(p))
            self.points.append((p[0], p[1]))
            self.max_power = max(p[1], self.max_power)
        assert self.points[0][0] == 0.0
        assert self.points[-1][0] == 100.0

    def power_from_soc(self, soc):
        """ Perform a lookup
        """
        assert soc >= 0 and soc <= 100

        for i, p in enumerate(self.points):
            if p[0] >= soc:
                if i == 0:
                    # first point
                    return p[1]
                else:
                    prev_point = self.points[i - 1]
                    soc_a = prev_point[0]
                    soc_b = p[0]
                    t = (soc - soc_a) / (soc_b - soc_a)
                    pow_a = prev_point[1]
                    pow_b = p[1]

                    # lerp
                    power = pow_a + (pow_b - pow_a) * t
                    return power

    def clamped(self, max_power):
        """ Return a new instance with clamped power
        """

        new_points = []
        for i, p in enumerate(self.points):
            if i + 1 >= len(self.points):
                new_points.append((p[0], min(max_power, p[1])))
                break
            next_point = self.points[i + 1]

            soc_a = p[0]
            soc_b = next_point[0]
            pow_a = p[1]
            pow_b = next_point[1]

            if pow_a <= max_power and pow_b <= max_power:
                new_points.append((p[0], p[1]))
            elif pow_a >= max_power and pow_b >= max_power:
                new_points.append((p[0], max_power))
            elif pow_a <= max_power and pow_b >= max_power:
                new_points.append((p[0], p[1]))
                # intersect lines
                t = (max_power - pow_a) / (pow_b - pow_a)
                soc = soc_a + (soc_b - soc_a) * t
                new_points.append((soc, max_power))
            elif pow_a >= max_power and pow_b <= max_power:
                new_points.append((p[0], max_power))
                # intersect lines
                t = (max_power - pow_a) / (pow_b - pow_a)
                soc = soc_a + (soc_b - soc_a) * t
                new_points.append((soc, max_power))

        return LoadingCurve(new_points)

    def __str__(self):
        return 'LoadingCurve {}'.format(vars(self))
