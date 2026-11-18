import json
from pathlib import Path
import pytest

from spice_ev import scenario, strategy
from .test_strategies import get_test_json

TEST_REPO_PATH = Path(__file__).parent


class TestBasic:
    def test_scenarios(self):
        # all scenarios with all battery strategie
        scenarios_dir = TEST_REPO_PATH / "test_data/input_test_strategies/"
        for scenario_filepath in scenarios_dir.glob("*scenario_*.json"):
            with scenario_filepath.open("r") as f:
                j = json.load(f)
            s = scenario.Scenario(j, scenarios_dir)
            for bat_strat in strategy.BATTERY_STRATEGIES:
                try:
                    s.run("balanced", {
                        "battery_strategy": bat_strat,
                        "time_windows": scenarios_dir / "time_windows_example.json",
                    })
                    assert s.step_i == s.n_intervals
                except Exception as e:
                    raise Exception(
                        f"Error with battery strategy {bat_strat} in {scenario_filepath.name}: {e}"
                    )

    def test_unknown_battery_strategy(self):
        j = get_test_json()
        s = scenario.Scenario(j)
        with pytest.raises(AssertionError):
            # battery strategy does not exist
            s.run("greedy", {"battery_strategy": "unknown"})

    def test_missing_timewindows(self):
        j = get_test_json()
        s = scenario.Scenario(j)
        with pytest.raises(Exception):
            # missing time windows file
            s.run("balanced", {"battery_strategy": "peak_load_window"})

    def test_distributed(self):
        scenario_filepath = TEST_REPO_PATH / "test_data/input_test_strategies/bus_scenario_D.json"
        with scenario_filepath.open("r") as f:
            j = json.load(f)
        s = scenario.Scenario(j, scenario_filepath.parent)
        s.run("distributed", {
            "strategy_deps": "balanced",
            "strategy_opps": "greedy",
            "strategy_options_deps": {"battery_strategy": "peak_shaving"},
        })

    def test_close_shave(self):
        # without battery support, GC is exceeded
        scenarios_dir = TEST_REPO_PATH / "test_data/input_test_strategies/"
        scenario_filepath = scenarios_dir / "scenario_2vehicles_building_pv_bat.json"
        with scenario_filepath.open("r") as f:
            j = json.load(f)
        # adjust GC power and battery capacity
        j["components"]["grid_connectors"]["GC1"]["max_power"] = 20
        j["components"]["batteries"]["BAT1"]["capacity"] = 250
        s = scenario.Scenario(j, scenarios_dir)
        # no battery strategy: GC maximum load exceeded
        s.run("balanced", {})
        assert s.step_i < s.n_intervals
        s.run("balanced", {"battery_strategy": "peak_shaving"})
        assert s.step_i == s.n_intervals
