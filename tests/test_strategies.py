from argparse import Namespace
import json
from pathlib import Path
import pytest

from spice_ev import scenario, strategy
from spice_ev.generate import generate_schedule

TEST_REPO_PATH = Path(__file__).parent


def get_test_json():
    # get minimum working json example
    return {
        "scenario": {
            "start_time": "2020-01-01T00:00:00+02:00",
            "interval": 15,
            "n_intervals": 96
        },
        "components": {
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


class TestCaseBase:
    def assertIsFile(self, path):
        assert path.exists()
        assert path.is_file()


class TestScenarios(TestCaseBase):

    def test_scenario_times(self):
        # correct number of timesteps?
        j = get_test_json()
        s = scenario.Scenario(j)
        assert s.n_intervals == 96

        # either n_intervals or stop time, not both
        j['scenario']['stop_time'] = "2020-01-01T01:00:00+02:00"
        with pytest.raises(AssertionError):
            s = scenario.Scenario(j)

        # remove n_intervals, stop time remains: success, four timesteps
        del j['scenario']['n_intervals']
        s = scenario.Scenario(j)
        assert s.n_intervals == 4

    def test_backwards_compatibility(self):
        # components used to be called constants
        j = get_test_json()
        j["constants"] = j.pop("components")
        scenario.Scenario(j)

    def test_empty(self):
        s = scenario.Scenario({
            "scenario": {
                "start_time": "2020-01-01T00:00:00+02:00",
                "interval": 15,
                "n_intervals": 10
            }})
        s.run('greedy', {})
        assert s.n_intervals == 10
        assert s.step_i == 10

    def test_file(self):
        # open from file
        input = TEST_REPO_PATH / 'test_data/input_test_strategies/scenario_A.json'
        scenario.Scenario(load_json(input), input.parent)

    # TEST SCENARIOS WITH BATTERY, FEEDIN AND EXTERNAL LOAD (Scenario A)
    def test_scenario_A(self):
        input = TEST_REPO_PATH / 'test_data/input_test_strategies/scenario_A.json'
        s = scenario.Scenario(load_json(input), input.parent)
        for strat in ['greedy', 'balanced', 'balanced_market', 'flex_window', 'peak_load_window']:
            s.run(strat, {})

    # TEST with battery, feedin, extLoad and V2G (Scenario B)
    def test_scenario_B(self):
        input = TEST_REPO_PATH / 'test_data/input_test_strategies/scenario_B.json'
        s = scenario.Scenario(load_json(input), input.parent)
        for strat in ['greedy', 'balanced', 'balanced_market', 'flex_window', 'peak_load_window']:
            s.run(strat, {})

    # TEST with battery, feedin, extLoad, V2G and schedule (Scenario C)
    def test_scenario_C1(self):
        input = TEST_REPO_PATH / 'test_data/input_test_strategies/scenario_C1.json'
        s = scenario.Scenario(load_json(input), input.parent)
        for strat in ['greedy', 'balanced', 'balanced_market', 'flex_window', 'peak_load_window']:
            s.run(strat, {"testing": True})
            for gcID, gc in s.components.grid_connectors.items():
                assert s.testing["max_total_load"] <= s.components.grid_connectors[gcID].max_power
                assert s.testing["max_total_load"] > 0

    def test_distributed_D(self):
        input = TEST_REPO_PATH / 'test_data/input_test_strategies/bus_scenario_D.json'
        s = scenario.Scenario(load_json(input), input.parent)
        s.run('distributed', {"testing": True, "strategy_option": [["ALLOW_NEGATIVE_SOC", True]],
                              "margin": 1})
        max_power = 0
        for gcID, gc in s.components.grid_connectors.items():
            max_power += s.components.grid_connectors[gcID].max_power
        assert s.testing["max_total_load"] <= max_power
        assert s.testing["max_total_load"] > 0

    def test_pv_bat(self):
        input = TEST_REPO_PATH / 'test_data/input_test_strategies/scenario_PV_Bat.json'
        s = scenario.Scenario(load_json(input), input.parent)
        s.run('greedy', {"testing": True})
        assert pytest.approx(s.testing["max_total_load"]) == 0
        assert s.testing["sum_feed_in_per_h"]["GC1"] == 246.0
        assert s.strat.world_state.batteries["BAT1"].soc > 0

    # TEST STRATEGY OUTPUTS
    def test_general_outputs(self):
        input = TEST_REPO_PATH / 'test_data/input_test_strategies/scenario_C1.json'
        s = scenario.Scenario(load_json(input), input.parent)
        s.run('greedy', {"testing": True})

        assert s.testing["avg_total_standing_time"]["GC1"] == 17.75
        assert s.testing["avg_stand_time"]["GC1"] == 8.875
        assert round(s.testing["avg_needed_energy"]["GC1"], 2) == 1.08
        assert round(s.testing["avg_drawn_power"]["GC1"], 2) == 1.44
        assert round(s.testing["sum_feed_in_per_h"]["GC1"], 2) == 0
        assert round(s.testing["vehicle_battery_cycles"]["GC1"], 2) == 1.1
        assert round(s.testing["avg_flex_per_window"]["GC1"][0], 2) == 372
        assert round(s.testing["avg_flex_per_window"]["GC1"][3], 2) == 375.71
        assert round(s.testing["sum_energy_per_window"]["GC1"][0], 2) == 0
        assert round(s.testing["sum_energy_per_window"]["GC1"][3], 2) == 0
        load = [0] * 96
        for key, values in s.testing["timeseries"]["loads"]["GC1"].items():
            load = [a + b for a, b in zip(load, values)]
        cs_load = [sum(item) for item in s.testing["timeseries"]["sum_cs"]]
        total_load = [a + b for a, b in zip(load, cs_load)]
        assert sum([round(a - b, 3) for a, b in zip(total_load, s.testing["timeseries"][
            "total_load"])]) == 0
        assert s.testing["max_total_load"] <= s.components.grid_connectors["GC1"].max_power
        assert s.testing["max_total_load"] > 0

    def test_flex_window_all_loaded_in_windows(self):
        input = TEST_REPO_PATH / 'test_data/input_test_strategies/scenario_C1.json'
        s = scenario.Scenario(load_json(input), input.parent)
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
        input = TEST_REPO_PATH / 'test_data/input_test_strategies/scenario_C2.json'
        s = scenario.Scenario(load_json(input), input.parent)
        s.run('flex_window', {"testing": True})

        # check if vehicles are loaded with max power in window
        cs_load = [sum(item) for item in s.testing["timeseries"]["sum_cs"]]
        indices_load_vehicle = [idx for idx, val in enumerate(cs_load) if val > 0]
        for idx in indices_load_vehicle:
            if s.testing["timeseries"]["schedule"]["GC1"][idx] is True:
                if round(cs_load[idx], 0) > 0:
                    assert round(cs_load[idx], 0) == s.components.charging_stations[
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
        input = TEST_REPO_PATH / 'test_data/input_test_strategies/scenario_C3.json'
        s = scenario.Scenario(load_json(input), input.parent)
        s.run('distributed', {"testing": True})
        max_power = 0
        for gcID, gc in s.components.grid_connectors.items():
            max_power += s.components.grid_connectors[gcID].max_power
        cs = s.testing["timeseries"]["sum_cs"]
        cs_1 = [x for x in cs if x[0] != 0]
        cs_2 = [x for x in cs if x[1] != 0]
        # only one cs at a time
        assert [x[1] == 0 for x in cs_1]
        assert [x[0] == 0 for x in cs_2]
        # assert that vehicles are charged balanced
        assert len(set([round(x[0], 2) for x in cs_1])) == 1
        assert len(set([round(x[1], 2) for x in cs_2])) == 1

    def test_distributed_C3_outputs(self, tmp_path):
        input = TEST_REPO_PATH / 'test_data/input_test_strategies/scenario_C3.json'
        save_results = tmp_path / 'save_results.json'
        save_timeseries = tmp_path / 'save_timeseries.csv'
        save_soc = tmp_path / 'save_soc.csv'
        s = scenario.Scenario(load_json(input), input.parent)
        s.run('distributed', {
            "testing": True,
            "save_results": save_results,
            "save_timeseries": save_timeseries,
            "save_soc": save_soc})
        self.assertIsFile(save_results)
        self.assertIsFile(save_timeseries)
        self.assertIsFile(save_soc)

    def test_schedule(self, tmp_path):
        for input in ["scenario_C3.json", "scenario_PV_Bat.json"]:
            # generate schedule -> copy scenario file to tmp
            src = TEST_REPO_PATH / f"test_data/input_test_strategies/{input}"
            dst = tmp_path / "scenario.json"
            dst.write_text(src.read_text())
            schedule = tmp_path / "schedule.csv"
            for load_strat in ["collective", "individual"]:
                # create schedule
                generate_schedule.generate_schedule(Namespace(
                    scenario=dst,
                    input=TEST_REPO_PATH/"test_data/input_test_generate/example_grid_situation.csv",
                    output=schedule,
                    individual=load_strat == "individual",
                    core_standing_time={
                        "times": [{"start": [22, 0], "end": [5, 0]}], "no_drive_days": [6]
                    },
                    priority_percentile=0.25,
                    visual=False,
                    config=None,
                ))
                with dst.open('r') as f:
                    j = json.load(f)
                s = scenario.Scenario(j, tmp_path)
                s.run('schedule', {"LOAD_STRAT": load_strat})

    def test_schedule_battery(self):
        test_json = {
            "scenario": {
                "start_time": "2020-01-01T00:00:00+02:00",
                "interval": 15,
                "n_intervals": 10
            },
            "components": {
                "grid_connectors": {
                    "GC": {
                        "max_power": 100,
                        "target": 5
                    }
                },
                "batteries": {
                    "BAT": {
                        "parent": "GC",
                        "charging_curve": [(0, 10), (1, 10)],
                        "capacity": 10,
                        "soc": 0.5,
                    }
                }
            },
        }
        s = scenario.Scenario(test_json)
        # schedule too high => charge battery (must not overflow)
        s.run('schedule', {"LOAD_STRAT": "individual", "testing": True})
        # test battery
        assert pytest.approx(s.batteryLevels["BAT"][-1]) == 10

        # schedule too low => discharge battery (must not become negative)
        s.components.grid_connectors["GC"].target = -5
        s.run('schedule', {"LOAD_STRAT": "individual", "testing": True})
        # test battery
        assert pytest.approx(s.batteryLevels["BAT"][-1]) == 0


def test_apply_battery_losses():
    test_json = {
        "scenario": {
            "start_time": "2020-01-01T00:00:00+02:00",
            "interval": 15,
            "n_intervals": 100
        },
        "components": {
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
    }
    s = scenario.Scenario(test_json)
    strat = strategy.Strategy(s.components, s.start_time, **{"interval": s.interval})
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
