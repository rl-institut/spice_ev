import json
import unittest
import os

from src import scenario


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


def load_json(filename):

    with open(filename, 'r') as f:
        return json.load(f)


class TestScenarios(unittest.TestCase):

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
        input = 'test_data/input_test_strategies/scenario_A.json'
        scenario.Scenario(load_json(input), os.path.dirname(input))

    # TEST SCENARIOS WITH BATTERY, FEEDIN AND EXTERNAL LOAD (Scenario A)

    def test_greedy_A(self):
        # test basic strategy
        input = 'test_data/input_test_strategies/scenario_A.json'
        s = scenario.Scenario(load_json(input), os.path.dirname(input))
        s.run('greedy', {})

    def test_balanced_A(self):
        # test basic strategy
        input = 'test_data/input_test_strategies/scenario_A.json'
        s = scenario.Scenario(load_json(input), os.path.dirname(input))
        s.run('balanced', {})

    def test_balanced_market_A(self):
        # test basic strategy
        input = 'test_data/input_test_strategies/scenario_A.json'
        s = scenario.Scenario(load_json(input), os.path.dirname(input))
        s.run('balanced_market', {})

    def test_flex_window_A(self):
        # test basic strategy
        input = 'test_data/input_test_strategies/scenario_A.json'
        s = scenario.Scenario(load_json(input), os.path.dirname(input))
        s.run('flex_window', {})

    def test_greedy_market_A(self):
        # test basic strategy
        input = 'test_data/input_test_strategies/scenario_A.json'
        s = scenario.Scenario(load_json(input), os.path.dirname(input))
        max_total_load = s.run('greedy_market', {})
        for gcID, gc in s.constants.grid_connectors.items():
            assert max_total_load <= s.constants.grid_connectors[gcID].max_power
            assert max_total_load > 0

    def test_peak_load_window_A(self):
        # test basic strategy
        input = 'test_data/input_test_strategies/scenario_A.json'
        s = scenario.Scenario(load_json(input), os.path.dirname(input))
        s.run('peak_load_window', {})

    # TEST with battery, feedin, extLoad and V2G (Scenario B)

    def test_greedy_B(self):
        input = 'test_data/input_test_strategies/scenario_B.json'
        s = scenario.Scenario(load_json(input), os.path.dirname(input))
        s.run('greedy', {})

    def test_balanced_B(self):
        input = 'test_data/input_test_strategies/scenario_B.json'
        s = scenario.Scenario(load_json(input), os.path.dirname(input))
        s.run('balanced', {})

    def test_balanced_market_B(self):
        input = 'test_data/input_test_strategies/scenario_B.json'
        s = scenario.Scenario(load_json(input), os.path.dirname(input))
        s.run('balanced_market', {})

    def test_flex_window_B(self):
        input = 'test_data/input_test_strategies/scenario_B.json'
        s = scenario.Scenario(load_json(input), os.path.dirname(input))
        s.run('flex_window', {})

    def test_greedy_market_B(self):
        input = 'test_data/input_test_strategies/scenario_B.json'
        s = scenario.Scenario(load_json(input), os.path.dirname(input))
        s.run('greedy_market', {})

    def test_peak_load_window_B(self):
        input = 'test_data/input_test_strategies/scenario_B.json'
        s = scenario.Scenario(load_json(input), os.path.dirname(input))
        s.run('peak_load_window', {})

    # TEST with battery, feedin, extLoad, V2G and schedule (Scenario C)

    def test_greedy_C(self):
        input = 'test_data/input_test_strategies/scenario_C.json'
        s = scenario.Scenario(load_json(input), os.path.dirname(input))
        max_total_load = s.run('greedy', {})
        for gcID, gc in s.constants.grid_connectors.items():
            assert max_total_load <= s.constants.grid_connectors[gcID].max_power
            assert max_total_load > 0

    def test_balanced_C(self):
        input = 'test_data/input_test_strategies/scenario_C.json'
        s = scenario.Scenario(load_json(input), os.path.dirname(input))
        max_total_load = s.run('balanced', {})
        for gcID, gc in s.constants.grid_connectors.items():
            assert max_total_load <= s.constants.grid_connectors[gcID].max_power
            assert max_total_load > 0

    def test_balanced_market_C(self):
        input = 'test_data/input_test_strategies/scenario_C.json'
        s = scenario.Scenario(load_json(input), os.path.dirname(input))
        max_total_load = s.run('balanced_market', {})
        for gcID, gc in s.constants.grid_connectors.items():
            assert max_total_load <= s.constants.grid_connectors[gcID].max_power
            assert max_total_load > 0

    def test_flex_window_C(self):
        input = 'test_data/input_test_strategies/scenario_C.json'
        s = scenario.Scenario(load_json(input), os.path.dirname(input))
        max_total_load = s.run('flex_window', {})
        for gcID, gc in s.constants.grid_connectors.items():
            assert max_total_load <= s.constants.grid_connectors[gcID].max_power
            assert max_total_load > 0

    def test_greedy_market_C(self):  # todo: does not work. why?
        input = 'test_data/input_test_strategies/scenario_B.json'
        s = scenario.Scenario(load_json(input), os.path.dirname(input))
        max_total_load = s.run('greedy_market', {})
        for gcID, gc in s.constants.grid_connectors.items():
            assert max_total_load <= s.constants.grid_connectors[gcID].max_power
            assert max_total_load > 0

    def test_peak_load_window_C(self):  # todo: this does not work with v2g
        input = 'test_data/input_test_strategies/scenario_C.json'
        s = scenario.Scenario(load_json(input), os.path.dirname(input))
        max_total_load = s.run('peak_load_window', {})
        for gcID, gc in s.constants.grid_connectors.items():
            assert max_total_load <= s.constants.grid_connectors[gcID].max_power
            assert max_total_load > 0


if __name__ == '__main__':
    unittest.main()
