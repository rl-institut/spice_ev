import json
import unittest
import os
import pytest

from src import scenario, strategy

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


class TestCaseBase(unittest.TestCase):
    def assertIsFile(self, path):
        if not os.path.isfile(path):
            raise AssertionError("File does not exist: %s" % str(path))


class TestScenarios(TestCaseBase):

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
        s.run('distributed', {"testing": True, "strategy_option": [["ALLOW_NEGATIVE_SOC", True]],
                              "margin": 1})
        max_power = 0
        for gcID, gc in s.constants.grid_connectors.items():
            max_power += s.constants.grid_connectors[gcID].max_power
        assert s.testing["max_total_load"] <= max_power
        assert s.testing["max_total_load"] > 0

    def test_pv_bat(self):
        input = os.path.join(TEST_REPO_PATH, 'test_data/input_test_strategies/scenario_PV_Bat.json')
        s = scenario.Scenario(load_json(input), os.path.dirname(input))
        s.run('greedy', {"testing": True})
        assert pytest.approx(s.testing["max_total_load"]) == 0
        assert s.testing["sum_feed_in_per_h"]["GC1"] == 246.0
        assert s.strat.world_state.batteries["BAT1"].soc > 0

    # TEST STRATEGY OUTPUTS
    def test_general_outputs(self):
        input = os.path.join(TEST_REPO_PATH, 'test_data/input_test_strategies/scenario_C1.json')
        s = scenario.Scenario(load_json(input), os.path.dirname(input))
        s.run('greedy', {"testing": True})

        assert s.testing["avg_total_standing_time"]["GC1"] == 17.5
        assert s.testing["avg_stand_time"]["GC1"] == 8.75
        assert round(s.testing["avg_needed_energy"]["GC1"], 2) == 1.08
        assert round(s.testing["avg_drawn_power"]["GC1"], 2) == 1.45
        assert round(s.testing["sum_feed_in_per_h"]["GC1"], 2) == 0
        assert round(s.testing["vehicle_battery_cycles"]["GC1"], 2) == 1.1
        assert round(s.testing["avg_flex_per_window"]["GC1"][0], 2) == 372
        assert round(s.testing["avg_flex_per_window"]["GC1"][3], 2) == 375.39
        assert round(s.testing["sum_energy_per_window"]["GC1"][0], 2) == 0
        assert round(s.testing["sum_energy_per_window"]["GC1"][3], 2) == 0
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
        indices_load_vehicle = [idx for idx, val in enumerate(cs_load) if round(val) > 0]
        for idx in indices_load_vehicle:
            assert s.testing["timeseries"]["schedule"]["GC1"][idx] is True
        # check if vehicles are only unloaded outside window
        indices_unload_vehicle = [idx for idx, val in enumerate(cs_load) if round(val, 2) < 0]
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

    def test_distributed_C3_priorization(self):
        input = os.path.join(TEST_REPO_PATH, 'test_data/input_test_strategies/scenario_C3.json')
        s = scenario.Scenario(load_json(input), os.path.dirname(input))
        s.run('distributed', {"testing": True, "strategy_option": [["ALLOW_NEGATIVE_SOC", True]],
                              "margin": 1})
        max_power = 0
        for gcID, gc in s.constants.grid_connectors.items():
            max_power += s.constants.grid_connectors[gcID].max_power
        cs = s.testing["timeseries"]["sum_cs"]
        cs_1 = [x for x in cs if x[0] != 0]
        cs_2 = [x for x in cs if x[1] != 0]
        # only one cs at a time
        assert [x[1] == 0 for x in cs_1]
        assert [x[0] == 0 for x in cs_2]
        # assert that cars are loaded balanced
        assert len(set([round(x[0], 2) for x in cs_1])) == 1
        assert len(set([round(x[1], 2) for x in cs_2])) == 1

    def test_distributed_C3_outputs(self):
        input = os.path.join(TEST_REPO_PATH, 'test_data/input_test_strategies/scenario_C3.json')
        save_results = os.path.join(TEST_REPO_PATH,
                                    'test_data/input_test_strategies/save_results.json')
        save_timeseries = os.path.join(TEST_REPO_PATH,
                                       'test_data/input_test_strategies/save_timeseries.csv')
        save_soc = os.path.join(TEST_REPO_PATH, 'test_data/input_test_strategies/save_soc.csv')
        s = scenario.Scenario(load_json(input), os.path.dirname(input))
        s.run('distributed', {"testing": True, "strategy_option": [["ALLOW_NEGATIVE_SOC", True]],
                              "save_results": save_results,
                              "save_timeseries": save_timeseries,
                              "save_soc": save_soc})
        self.assertIsFile(save_results)
        self.assertIsFile(save_timeseries)
        self.assertIsFile(save_soc)
        # remove output file
        os.remove(save_results)
        os.remove(save_timeseries)
        os.remove(save_soc)


def test_apply_battery_losses():
    test_json = {
        "scenario": {
            "start_time": "2020-01-01T00:00:00+02:00",
            "interval": 15,
            "n_intervals": 100
        },
        "constants": {
            "grid_connectors": {},
            "charging_stations": {},
            "vehicle_types": {
                "test": {
                    "name": "test",
                    "capacity": 100,
                    "charging_curve": [(0, 1), (1, 1)],
                    "loss_rate": None
                }
            },
            "vehicles": {
                "test_vehicle": {"vehicle_type": "test", "soc": 1}
            },
            "batteries": {
                "test_battery": {
                    "parent": "GC",
                    "charging_curve": [(0, 1), (1, 1)],
                    "capacity": 100,
                    "soc": 1,
                    "loss_rate": None
                }
            }
        },
        "events": {
            "external_loads": {},
            "grid_operator_signals": [],
            "vehicle_events": [],
        }
    }
    s = scenario.Scenario(test_json)
    strat = strategy.Strategy(s.constants, s.start_time, **{"interval": s.interval})
    # test vehicle battery in particular
    battery = strat.world_state.vehicles["test_vehicle"].battery

    # no loss rate -> no change
    strat.apply_battery_losses()
    assert battery.soc == 1

    # empty dict -> no change
    battery.loss_rate = {}
    strat.apply_battery_losses()
    assert battery.soc == 1

    # relative loss rate = 10 -> 10% of SoC per timestep lost
    battery.soc = 1
    battery.loss_rate = {"relative": 10}
    for i in range(10):
        strat.apply_battery_losses()
        assert battery.soc == pytest.approx(0.9**(i+1))

    # fixed relative loss rate = 10 -> flat 10% SoC per timestep lost
    battery.soc = 1
    battery.loss_rate = {"fixed_relative": 10}
    for i in range(10):
        strat.apply_battery_losses()
        assert battery.soc == pytest.approx(1 - (i+1)*0.1)

    # fixed absolute loss rate = 5 => flat 5 kWh per timestep lost
    battery.soc = 1
    battery.loss_rate = {"fixed_absolute": 5}
    for i in range(10):
        strat.apply_battery_losses()
        assert battery.soc == pytest.approx(1 - (i+1)*0.05)

    # combined loss rates
    battery.soc = 0.6
    battery.capacity = 30
    loss_rate = {
        "relative": 50,
        "fixed_relative": 5,
        "fixed_absolute": 3
    }
    battery.loss_rate = loss_rate
    strat.apply_battery_losses()
    # new soc: 0.6 - (50%*0.6) - (5%) - (3kWh/30kWh) = 0.15
    assert pytest.approx(battery.soc) == 0.15

    # no loss on empty battery
    battery.soc = 0
    strat.apply_battery_losses()
    assert battery.soc == 0

    # test stationary battery, only combined test (should behave identical)
    battery = strat.world_state.batteries["test_battery"]
    battery.loss_rate = loss_rate
    strat.apply_battery_losses()
    # new soc: 1.0 - (50%*1.0) - (5%) - (3kWh/100kWh) = 0.42
    assert pytest.approx(battery.soc) == 0.42


if __name__ == '__main__':
    unittest.main()
