import json
import unittest
import scenario


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

    def test_file(self):
        with open('tests/test_scenario.json', 'r') as f:
            s = scenario.Scenario(json.load(f), 'tests/')

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


if __name__ == '__main__':
    unittest.main()
