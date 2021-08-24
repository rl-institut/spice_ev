import datetime
import json
import unittest

from src import battery, loading_curve, scenario, util


def get_test_json():
    # get minimum working json example
    return {
        "scenario": {
            "start_time": "2020-01-01T00:00:00+02:00",
            "interval": 15,
            "n_intervals": 35040
        },
        "constants": {
            "grid_connectors": {},
            "charging_stations": {},
            "vehicle_types": {},
            "vehicles": {},
        },
        "events": {
            "external_loads": {},
            "grid_operator_signals": [],
            "vehicle_events": [],
        },
        "strategy": "greedy"
    }


class TestScenario(unittest.TestCase):

    def test_scenario_times(self):
        # corect number of timesteps?
        j = get_test_json()
        s = scenario.Scenario(j)
        self.assertEqual(s.n_intervals, 35040)

        # either n_intervals or stop time, not both
        j['scenario']['stop_time'] = "2020-01-01T01:00:00+02:00"
        with self.assertRaises(AssertionError):
            s = scenario.Scenario(j)

        # remove n_intervals, stop time remains: success, four timesteps
        del j['scenario']['n_intervals']
        s = scenario.Scenario(j)
        self.assertEqual(s.n_intervals, 4)

    def test_file(self):
        # open from file
        with open('examples/scenario.json', 'r') as f:
            scenario.Scenario(json.load(f), "")

    def test_greedy(self):
        # test basic strategy
        s = scenario.Scenario(get_test_json(), None)
        s.run('greedy', {})


def approx_eq(x, y, eps=1e-3):
    return abs(x - y) < eps


class TestLoadingCurve(unittest.TestCase):
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


class TestBattery(unittest.TestCase):
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
        points = [(0, 42), (0.5, 42), (1, 0)]
        lc = loading_curve.LoadingCurve(points)
        b1 = battery.Battery(100, lc, 0)
        b2 = battery.Battery(100, lc, 0)
        td = datetime.timedelta(hours=1)
        p1 = b1.load(td, 10)["avg_power"]
        p2 = 0
        for _ in range(10):
            p2 += b2.load(td, 1)["avg_power"]
        assert approx_eq(b1.soc, b2.soc), "SoC different: {} vs {}".format(b1.soc, b2.soc)
        assert approx_eq(p1, p2), "Used power different: {} vs {}".format(p1, p2)


class UtilTest(unittest.TestCase):

    def test_time_window(self):

        time_windows = {
            "autumn": {
                # 01.09. - 30.11., 16:30 - 20:00
                "start": datetime.datetime(day=1, month=9, year=1900, hour=16, minute=30),
                "end": datetime.datetime(day=30, month=11, year=1900, hour=20, minute=0),
            },
            # split winter (year changes)
            "winter1": {
                # 01.12. - 31.12., 16:30 - 19:30
                "start": datetime.datetime(day=1, month=12, year=1900, hour=16, minute=30),
                "end": datetime.datetime(day=31, month=12, year=1900, hour=19, minute=30),
            },
            "winter2": {
                # 01.01. - 28.02., 16:30 - 19:30
                "start": datetime.datetime(day=1, month=1, year=1900, hour=16, minute=30),
                "end": datetime.datetime(day=28, month=2, year=1900, hour=19, minute=30),
            }
        }

        not_in_window = [
            datetime.datetime(2021, 8, 31, 23, 59),
            datetime.datetime(2022, 9, 1, 0, 00),
            datetime.datetime(2023, 12, 31, 20, 0),
            datetime.datetime(2024, 9, 1, 20, 0),
        ]
        in_window = [
            datetime.datetime(2021, 9, 1, 16, 30),
            datetime.datetime(2022, 9, 3, 19, 59),
            datetime.datetime(2023, 1, 1, 18, 0),
        ]

        for dt in not_in_window:
            assert not util.datetime_within_window(dt, time_windows)
        for dt in in_window:
            assert util.datetime_within_window(dt, time_windows)


if __name__ == '__main__':
    unittest.main()
