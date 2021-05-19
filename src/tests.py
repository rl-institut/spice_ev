import datetime
import json
import unittest

from src import battery, loading_curve, scenario


def get_test_json():
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
        j = get_test_json()
        s = scenario.Scenario(j)
        self.assertEqual(s.n_intervals, 35040)

        j['scenario']['stop_time'] = "2020-01-01T01:00:00+02:00"
        with self.assertRaises(AssertionError):
            s = scenario.Scenario(j)

        del j['scenario']['n_intervals']
        s = scenario.Scenario(j)
        self.assertEqual(s.n_intervals, 4)

    def test_file(self):
        with open('examples/scenario.json', 'r') as f:
            scenario.Scenario(json.load(f), "")

    def test_greedy(self):
        s = scenario.Scenario(get_test_json(), None)
        s.run('greedy', {})


def approx_eq(x, y, eps=1e-3):
    return abs(x - y) < eps


class TestLoadingCurve(unittest.TestCase):
    def test_creation(self):
        points = [(50.0, 100.0), (0.0, 100.0), (100.0, 0.0)]
        lc = loading_curve.LoadingCurve(points)
        assert lc.points[0][0] == 0.0

    def test_power_from_soc(self):
        points = [(0.0, 100.0), (50.0, 100.0), (100.0, 0.0)]
        lc = loading_curve.LoadingCurve(points)

        assert lc.power_from_soc(0) == 100.0
        assert lc.power_from_soc(25) == 100.0
        assert lc.power_from_soc(50) == 100.0
        assert lc.power_from_soc(75) == 50.0
        assert lc.power_from_soc(100) == 0.0
        assert lc.power_from_soc(100) != 100

    def test_clamp(self):
        points = [(0.0, 100.0), (50.0, 100.0), (100.0, 0.0)]
        lc = loading_curve.LoadingCurve(points)
        lc2 = lc.clamped(100)

        for x in range(101):
            assert lc.power_from_soc(x) == lc2.power_from_soc(x)

        lc2 = lc.clamped(75)
        for x in range(101):
            assert approx_eq(min(75, lc.power_from_soc(x)), lc2.power_from_soc(x))

        points = [(0.0, 100.0), (50.0, 50), (100.0, 0.0)]
        lc = loading_curve.LoadingCurve(points)
        lc2 = lc.clamped(75)
        for x in range(101):
            assert approx_eq(min(75, lc.power_from_soc(x)), lc2.power_from_soc(x))


class TestBattery(unittest.TestCase):
    def test_creation(self):
        points = [(0.0, 100.0), (50.0, 100.0), (100.0, 0.0)]
        lc = loading_curve.LoadingCurve(points)
        bat = battery.Battery(100, lc, 0)
        print(bat)

    """
    def test_load(self):
        import matplotlib.pyplot as plt

        points = [(0.0, 100.0), (50.0, 100.0), (100.0, 0.0)]
        lc = loading_curve.LoadingCurve(points)
        bat = battery.Battery(100, lc, 0)

        x = []
        y = []
        tdelta  = datetime.timedelta(seconds=1)
        for t in range(3600*3):
            x.append(t)
            y.append(bat.soc)
            bat.load(tdelta, 100)

        fig, ax = plt.subplots()
        ax.plot(x, y)
        plt.show()
    """

    def test_charging(self):
        # charge one battery with 10 kW for one hour and another battery with 1 kW for 10 hours
        # SoC and used energy must be the same
        points = [(0.0, 100.0), (50.0, 100.0), (100.0, 0.0)]
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


if __name__ == '__main__':
    unittest.main()
