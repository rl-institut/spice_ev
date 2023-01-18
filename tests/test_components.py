import datetime
import pytest

from spice_ev import battery, loading_curve


def approx_eq(x, y, eps=1e-3):
    return abs(x - y) < eps


class TestLoadingCurve:
    def test_creation(self):
        # basic loading curve
        points = [(0.5, 42), (0, 42), (1, 0)]
        lc = loading_curve.LoadingCurve(points)
        assert lc.points[0][0] == 0.0

    def test_power_from_soc(self):
        # test correct inverse
        points = [(0, 42), (0.5, 42), (1, 0)]
        lc = loading_curve.LoadingCurve(points)

        assert lc.power_from_soc(0.00) == 42.0
        assert lc.power_from_soc(0.25) == 42.0
        assert lc.power_from_soc(0.50) == 42.0
        assert lc.power_from_soc(0.75) == 21.0
        assert lc.power_from_soc(1.00) == 0.0

        # simple line
        points = [(0, 1), (1, 0)]
        lc = loading_curve.LoadingCurve(points)
        for x in range(101):
            assert lc.power_from_soc(x/100) == 1 - x/100

    def test_clamp(self):
        # test clamped loading curve
        points = [(0, 42), (0.5, 42), (1, 0)]
        lc = loading_curve.LoadingCurve(points)
        lc2 = lc.clamped(42)

        # clamped at max value: no change
        for x in range(101):
            assert lc.power_from_soc(x/100) == lc2.power_from_soc(x/100)

        # clamped below max value: take min
        lc2 = lc.clamped(32)
        for x in range(101):
            assert approx_eq(min(32, lc.power_from_soc(x/100)), lc2.power_from_soc(x/100))


class TestBattery:
    def test_creation(self):
        # basic battery instance
        points = [(0, 42), (0.5, 42), (1, 0)]
        lc = loading_curve.LoadingCurve(points)
        bat = battery.Battery(100, lc, 0)
        print(bat)

    """
    def test_load(self):
        import matplotlib.pyplot as plt

        points = [(0, 42), (0.5, 42), (1, 0)]
        lc = loading_curve.LoadingCurve(points)
        bat = battery.Battery(42, lc, 0)

        x = []
        y = []
        tdelta  = datetime.timedelta(seconds=1)
        for t in range(3600*3):
            x.append(t)
            y.append(bat.soc)
            bat.load(tdelta, 42)

        fig, ax = plt.subplots()
        ax.plot(x, y)
        plt.show()
    """

    def test_charging(self):
        # charge one battery with 10 kW for one hour and another battery with 1 kW for 10 hours
        # SoC and used energy must be the same
        points = [(0, 42), (0.5, 42), (1, 1)]
        lc = loading_curve.LoadingCurve(points)
        # charge from soc=0
        b1 = battery.Battery(100, lc, 0)
        b2 = battery.Battery(100, lc, 0)
        td = datetime.timedelta(hours=1)
        p1 = b1.load(td, 10)["avg_power"]
        p2 = 0
        for _ in range(10):
            p2 += b2.load(td, 1)["avg_power"]
        assert approx_eq(b1.soc, b2.soc), "SoC different: {} vs {}".format(b1.soc, b2.soc)
        assert approx_eq(p1, p2), "Used power different: {} vs {}".format(p1, p2)
        # discharge from soc=0, allow discharge below soc=0
        # make sure loading piecewise gives same result as loading once for same duration
        b1 = battery.Battery(100, lc, 0, unloading_curve=lc)
        b2 = battery.Battery(100, lc, 0, unloading_curve=lc)
        td = datetime.timedelta(hours=1)
        p1 = b1.unload(td, target_soc=-float('inf'))["avg_power"]
        p2 = 0
        for _ in range(10):
            p2 += b2.unload(td, p1/10, target_soc=-float('inf'))["avg_power"]
        assert approx_eq(b1.soc, b2.soc), "SoC different: {} vs {}".format(b1.soc, b2.soc)
        assert approx_eq(p1, p2), "Used power different: {} vs {}".format(p1, p2)

        # make sure battery does not charge over soc=1
        b1 = battery.Battery(100, lc, 0.9)
        b2 = battery.Battery(100, lc, 0.9)
        td = datetime.timedelta(hours=1)
        p1 = b1.load(td*10, 42)["avg_power"]
        p2 = 0
        for _ in range(10):
            p2 += b2.load(td, 42)["avg_power"]
        p2 /= 10
        assert approx_eq(b1.soc, 1), "SoC should be 1 but is: {}".format(b1.soc)
        assert approx_eq(b2.soc, 1), "SoC should be 1 but is: {}".format(b2.soc)
        assert approx_eq(p1, p2), "Used power different: {} vs {}".format(p1, p2)

        # check that loading and unloading behave the same given same charging curve
        b1 = battery.Battery(100, lc, 0, efficiency=1)
        b2 = battery.Battery(100, lc, 1, efficiency=1, unloading_curve=lc)
        td = datetime.timedelta(minutes=15)
        t1 = t2 = 0
        while not approx_eq(b1.soc, 1):
            b1.load(td, 42)["avg_power"]
            t1 += 1
        while not approx_eq(b2.soc, 0):
            b2.unload(td, target_soc=0)["avg_power"]
            t2 += 1
        assert t1 == t2, "Loading(0-100) and unloading(100-0) processes vary in duration"

    def test_initial_soc_negative(self):
        # tests for charging and discharging if initial soc < 0
        # for soc < 0 the virtual charging curve is constant
        # tests confirm two things:
        # 1. The amount of energy (un)loaded within an hour is correct
        # 2. With sufficient time to charge, the battery reaches target soc
        td_short = datetime.timedelta(hours=1)
        td_long = datetime.timedelta(hours=100)

        points = [(0, 42), (0.5, 42), (1, 0)]
        lc = loading_curve.LoadingCurve(points)
        capacity = 100
        efficiency = .5
        initial_soc = -0.5
        b = battery.Battery(capacity, lc, 0, efficiency, lc)

        # test charging
        # target = (target_soc, expected power after 1 hour, expected SoC afer 1 hour)
        target = [
            (-0.6, 0, -0.5),
            (-0.2, 10, -0.45),
            (0, 10, -0.45),
            (0.5, 10, -0.45),
        ]
        for t in target:
            b.soc = initial_soc
            p = b.load(td_short, 10, target_soc=t[0])["avg_power"]
            assert approx_eq(p, t[1])
            assert approx_eq(b.soc, t[2])
            p = b.load(td_long, 50, target_soc=t[0])["avg_power"]
            assert approx_eq(b.soc, max(initial_soc, t[0]))

        # test discharging
        # target = (target_soc, expected power after 1 hour, expected SoC afer 1 hour)
        target = [
            (-1, 10, -0.7),
            (-0.5, 0, -0.5),
            (0, 0, -0.5),
            (0.5, 0, -0.5),
        ]
        for t in target:
            b.soc = initial_soc
            p = b.unload(td_short, 10, target_soc=t[0])["avg_power"]
            assert approx_eq(p, t[1])
            assert approx_eq(b.soc, t[2])
            p = b.unload(td_long, 50, target_soc=t[0])["avg_power"]
            assert approx_eq(b.soc, min(initial_soc, t[0]))

    def test_differential(self):
        # test non-constant (dis)charging curve
        points = [(0, 0), (1, 1)]
        lc = loading_curve.LoadingCurve(points)
        capacity = 1
        efficiency = 1
        initial_soc = 0.1
        b = battery.Battery(capacity, lc, initial_soc, efficiency, lc)
        b.unload(datetime.timedelta(hours=1))

    def test_unload(self):
        points = [(0, 0), (1, 10)]
        lc = loading_curve.LoadingCurve(points)
        capacity = 10
        efficiency = 0.75
        initial_soc = 0.5
        b = battery.Battery(capacity, lc, initial_soc, efficiency, lc)
        td = datetime.timedelta(hours=1)
        # simple unloading
        pwr = b.unload(td)['avg_power']
        assert pytest.approx(pwr, 2) == 2.76
        b.soc = 0.5
        # target soc
        pwr = b.unload(td, target_soc=0.4)['avg_power']
        assert pytest.approx(pwr) == 0.75
        assert b.soc == 0.4
        b.soc = 0.5
        # target power
        pwr = b.unload(td, target_power=1)['avg_power']
        assert pytest.approx(pwr) == 1
        # target soc and power not allowed
        with pytest.raises(AssertionError):
            b.unload(td, target_soc=1, target_power=1)
