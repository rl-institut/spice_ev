import json
import unittest
import os

from src import scenario

TEST_REPO_PATH = os.path.dirname(__file__)


def get_test_json():
    # get minimum working json example
    return {
        "scenario": {
            "start_time": "2020-01-01T00:00:00+02:00",
            "interval": 15,
            "n_intervals": 96
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
        }
    }


def load_json(filename):

    with open(filename, 'r') as f:
        return json.load(f)


class TestScenarios(unittest.TestCase):

    def test_scenario_times(self):
        # correct number of timesteps?
        j = get_test_json()
        s = scenario.Scenario(j)
        self.assertEqual(s.n_intervals, 96)

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
        input = os.path.join(TEST_REPO_PATH, 'test_data/input_test_strategies/scenario_A.json')
        scenario.Scenario(load_json(input), os.path.dirname(input))

    # TEST SCENARIOS WITH BATTERY, FEEDIN AND EXTERNAL LOAD (Scenario A)

    def test_greedy_A(self):
        # test basic strategy
        input = os.path.join(TEST_REPO_PATH, 'test_data/input_test_strategies/scenario_A.json')
        s = scenario.Scenario(load_json(input), os.path.dirname(input))
        s.run('greedy', {})

    def test_balanced_A(self):
        # test basic strategy
        input = os.path.join(TEST_REPO_PATH, 'test_data/input_test_strategies/scenario_A.json')
        s = scenario.Scenario(load_json(input), os.path.dirname(input))
        s.run('balanced', {})

    def test_balanced_market_A(self):
        # test basic strategy
        input = os.path.join(TEST_REPO_PATH, 'test_data/input_test_strategies/scenario_A.json')
        s = scenario.Scenario(load_json(input), os.path.dirname(input))
        s.run('balanced_market', {})

    def test_flex_window_A(self):
        # test basic strategy
        input = os.path.join(TEST_REPO_PATH, 'test_data/input_test_strategies/scenario_A.json')
        s = scenario.Scenario(load_json(input), os.path.dirname(input))
        s.run('flex_window', {})

    def test_peak_load_window_A(self):
        # test basic strategy
        input = os.path.join(TEST_REPO_PATH, 'test_data/input_test_strategies/scenario_A.json')
        s = scenario.Scenario(load_json(input), os.path.dirname(input))
        s.run('peak_load_window', {})

    # TEST with battery, feedin, extLoad and V2G (Scenario B)

    def test_greedy_B(self):
        input = os.path.join(TEST_REPO_PATH, 'test_data/input_test_strategies/scenario_B.json')
        s = scenario.Scenario(load_json(input), os.path.dirname(input))
        s.run('greedy', {})

    def test_balanced_B(self):
        input = os.path.join(TEST_REPO_PATH, 'test_data/input_test_strategies/scenario_B.json')
        s = scenario.Scenario(load_json(input), os.path.dirname(input))
        s.run('balanced', {})

    def test_balanced_market_B(self):
        input = os.path.join(TEST_REPO_PATH, 'test_data/input_test_strategies/scenario_B.json')
        s = scenario.Scenario(load_json(input), os.path.dirname(input))
        s.run('balanced_market', {})

    def test_flex_window_B(self):
        input = os.path.join(TEST_REPO_PATH, 'test_data/input_test_strategies/scenario_B.json')
        s = scenario.Scenario(load_json(input), os.path.dirname(input))
        s.run('flex_window', {})

    def test_peak_load_window_B(self):
        input = os.path.join(TEST_REPO_PATH, 'test_data/input_test_strategies/scenario_B.json')
        s = scenario.Scenario(load_json(input), os.path.dirname(input))
        s.run('peak_load_window', {})

    # TEST with battery, feedin, extLoad, V2G and schedule (Scenario C)

    def test_balanced_C(self):
        input = os.path.join(TEST_REPO_PATH, 'test_data/input_test_strategies/scenario_C1.json')
        s = scenario.Scenario(load_json(input), os.path.dirname(input))
        s.run('balanced', {"testing": True})
        for gcID, gc in s.constants.grid_connectors.items():
            assert s.testing["max_total_load"] <= s.constants.grid_connectors[gcID].max_power
            assert s.testing["max_total_load"] > 0

    def test_balanced_market_C(self):
        input = os.path.join(TEST_REPO_PATH, 'test_data/input_test_strategies/scenario_C1.json')
        s = scenario.Scenario(load_json(input), os.path.dirname(input))
        s.run('balanced_market', {"testing": True})
        for gcID, gc in s.constants.grid_connectors.items():
            assert s.testing["max_total_load"] <= s.constants.grid_connectors[gcID].max_power
            assert s.testing["max_total_load"] > 0

    def test_flex_window_C(self):
        input = os.path.join(TEST_REPO_PATH, 'test_data/input_test_strategies/scenario_C1.json')
        s = scenario.Scenario(load_json(input), os.path.dirname(input))
        s.run('flex_window', {"testing": True})
        for gcID, gc in s.constants.grid_connectors.items():
            assert s.testing["max_total_load"] <= s.constants.grid_connectors[gcID].max_power
            assert s.testing["max_total_load"] > 0

    def test_peak_load_window_C(self):
        input = os.path.join(TEST_REPO_PATH, 'test_data/input_test_strategies/scenario_C1.json')
        s = scenario.Scenario(load_json(input), os.path.dirname(input))
        s.run('peak_load_window', {"testing": True})
        for gcID, gc in s.constants.grid_connectors.items():
            assert s.testing["max_total_load"] <= s.constants.grid_connectors[gcID].max_power
            assert s.testing["max_total_load"] > 0

    def test_distributed_D(self):
        input = os.path.join(TEST_REPO_PATH, 'test_data/input_test_strategies/bus_scenario_D.json')
        s = scenario.Scenario(load_json(input), os.path.dirname(input))
        s.run('distributed', {"testing": True, "strategy_option": [["ALLOW_NEGATIVE_SOC", True]]})
        max_power = 0
        for gcID, gc in s.constants.grid_connectors.items():
             max_power += s.constants.grid_connectors[gcID].max_power
        assert s.testing["max_total_load"] <= max_power
        assert s.testing["max_total_load"] > 0

    # TEST STRATEGY OUTPUTS

    def test_general_outputs(self):
        input = os.path.join(TEST_REPO_PATH, 'test_data/input_test_strategies/scenario_C1.json')
        s = scenario.Scenario(load_json(input), os.path.dirname(input))
        s.run('greedy', {"testing": True})

        assert s.testing["avg_total_standing_time"]["GC1"] == 17.5
        assert s.testing["avg_stand_time"]["GC1"] == 8.75
        assert round(s.testing["avg_needed_energy"]["GC1"], 2) == 2.7
        assert round(s.testing["avg_drawn_pwer"]["GC1"], 2) == 10.69
        assert round(s.testing["sum_feed_in_per_h"]["GC1"], 2) == 347.59
        assert round(s.testing["vehicle_battery_cycles"]["GC1"], 2) == 2.12
        assert round(s.testing["avg_flex_per_window"]["GC1"][0], 2) == 373.7
        assert round(s.testing["avg_flex_per_window"]["GC1"][3], 2) == 382.38
        assert round(s.testing["sum_energy_per_window"]["GC1"][0], 2) == 215.87
        assert round(s.testing["sum_energy_per_window"]["GC1"][3], 2) == 33.11
        load = [0] * 96
        for key, values in s.testing["timeseries"]["loads"]["GC1"].items():
            load = [a + b for a, b in zip(load, values)]
        cs_load = [sum(item) for item in s.testing["timeseries"]["sum_cs"]]
        total_load = [a + b for a, b in zip(load, cs_load)]
        assert sum([round(a - b, 3) for a, b in zip(total_load, s.testing["timeseries"][
            "total_load"])]) == 0
        assert s.testing["max_total_load"] <= s.constants.grid_connectors["GC1"].max_power
        assert s.testing["max_total_load"] > 0

    def test_flex_window_all_loaded_in_windows(self):
        input = os.path.join(TEST_REPO_PATH, 'test_data/input_test_strategies/scenario_C1.json')
        s = scenario.Scenario(load_json(input), os.path.dirname(input))
        s.run('flex_window', {"testing": True})

        # check if vehicles are only loaded in window
        cs_load = [sum(item) for item in s.testing["timeseries"]["sum_cs"]]
        indices_load_vehicle = [idx for idx, val in enumerate(cs_load) if val > 0]
        for idx in indices_load_vehicle:
            assert s.testing["timeseries"]["schedule"]["GC1"][idx] is True
        # check if vehicles are only unloaded outside window
        indices_unload_vehicle = [idx for idx, val in enumerate(cs_load) if val < 0]
        for idx in indices_unload_vehicle:
            assert s.testing["timeseries"]["schedule"]["GC1"][idx] is False
        # check if batteries are only loaded in window
        indices_load_battery = [idx for idx, val in enumerate(s.testing["timeseries"]["loads"]
                                                              ["GC1"]["BAT1"]) if val > 0]
        for idx in indices_load_battery:
            assert s.testing["timeseries"]["schedule"]["GC1"][idx] is True
        # check if batteries are only unloaded outside window
        indices_unload_battery = [idx for idx, val in enumerate(s.testing["timeseries"]["loads"]
                                                                ["GC1"]["BAT1"]) if val < 0]
        for idx in indices_unload_battery:
            assert s.testing["timeseries"]["schedule"]["GC1"][idx] is False

    def test_flex_window_not_loaded_in_windows(self):
        input = os.path.join(TEST_REPO_PATH, 'test_data/input_test_strategies/scenario_C2.json')
        s = scenario.Scenario(load_json(input), os.path.dirname(input))
        s.run('flex_window', {"testing": True})

        # check if vehicles are loaded with max power in window
        cs_load = [sum(item) for item in s.testing["timeseries"]["sum_cs"]]
        indices_load_vehicle = [idx for idx, val in enumerate(cs_load) if val > 0]
        for idx in indices_load_vehicle:
            if s.testing["timeseries"]["schedule"]["GC1"][idx] is True:
                if round(cs_load[idx], 0) > 0:
                    assert round(cs_load[idx], 0) == s.constants.charging_stations[
                        "CS_golf_0"].max_power
        # check if batteries are only loaded in window
        indices_load_battery = [idx for idx, val in enumerate(s.testing["timeseries"]["loads"]
                                                              ["GC1"]["BAT1"]) if val > 0]
        for idx in indices_load_battery:
            assert s.testing["timeseries"]["schedule"]["GC1"][idx] is True
        # check if batteries are only unloaded outside window
        indices_unload_battery = [idx for idx, val in enumerate(s.testing["timeseries"]["loads"]
                                                                ["GC1"]["BAT1"]) if val < 0]
        for idx in indices_unload_battery:
            assert s.testing["timeseries"]["schedule"]["GC1"][idx] is False


if __name__ == '__main__':
    unittest.main()