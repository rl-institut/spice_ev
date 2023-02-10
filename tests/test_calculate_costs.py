import json
import pytest
from pathlib import Path
import subprocess

from spice_ev import scenario, costs as cc
from calculate_costs import read_simulation_csv

TEST_REPO_PATH = Path(__file__).parent
supported_strategies = ["greedy", "balanced", "distributed", "balanced_market",
                        "schedule", "flex_window"]


def get_test_json():
    # get minimum working json example
    return {
        "scenario": {
            "start_time": "2020-01-01T00:00:00+02:00",
            "interval": 15,
            "n_intervals": 96
        },
        "components": {
            "grid_connectors": {
                "GC1": {"max_power": 100, "cost": {"type": "fixed", "value": 0}}
            },
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


class TestSimulationCosts:
    def test_read_sim_csv(self, tmp_path):
        j = get_test_json()
        s = scenario.Scenario(j)
        save_timeseries = tmp_path / "save_timeseries.csv"
        s.run('greedy', {"save_timeseries": str(save_timeseries)})
        result = read_simulation_csv(str(save_timeseries))

        # check length of result lists
        for k, l in result.items():
            assert len(l) == s.n_intervals, f"list {k} has wrong length"

        # check individual lists
        # timestamps
        assert result["timestamps_list"][0] == s.start_time.replace(tzinfo=None)
        assert result["timestamps_list"][-1] == (s.stop_time - s.interval).replace(tzinfo=None)
        # price: all zeroes
        assert sum(result["price_list"]) == 0
        # grid supply: 0
        assert sum(result["power_grid_supply_list"]) == 0
        # fix load: 0
        assert sum(result["power_fix_load_list"]) == 0
        # charging signal: depends on schedule, should be all None
        assert not any(result["charging_signal_list"])

    def test_calculate_costs_basic(self):
        j = get_test_json()
        s = scenario.Scenario(j)
        s.run('greedy', {"cost_calculation": True})
        timeseries = s.GC1_timeseries
        timeseries_lists = [timeseries.get(k, [0]*s.n_intervals) for k in [
                            "time", "grid supply [kW]", "price [EUR/kWh]",
                            "local generation [kW]", "window signal [-]"]]
        price_sheet = TEST_REPO_PATH / 'test_data/input_test_cost_calculation/price_sheet.json'

        # test all supported strategies
        for strategy in supported_strategies:
            cc.calculate_costs(strategy, "MV", s.interval, *timeseries_lists,
                               core_standing_time_dict=s.core_standing_time,
                               price_sheet_json=str(price_sheet))

        # test error for non-supported strategy
        with pytest.raises(Exception):
            cc.calculate_costs("strategy", "MV", s.interval, *timeseries_lists,
                               core_standing_time_dict=s.core_standing_time,
                               price_sheet_json=str(price_sheet))

        # check returned values
        result = cc.calculate_costs(supported_strategies[0], "MV", s.interval, *timeseries_lists,
                                    core_standing_time_dict=s.core_standing_time,
                                    price_sheet_json=str(price_sheet))
        assert result["total_costs_per_year"] == 78.18
        assert result["commodity_costs_eur_per_year"] == 0
        assert result["capacity_costs_eur"] == 65.7
        assert result["power_procurement_costs_per_year"] == 0
        assert result["levies_fees_and_taxes_per_year"] == 12.48
        assert result["feed_in_remuneration_per_year"] == 0

    def test_calculate_costs_advanced(self):

        scenarios = {
            "scenario_A.json": [2522.67, 776.54, 65.7, 799.38, 881.06, 0.0],
            "scenario_B.json": [21798.48, 6899.86, 65.7, 7102.8, 7730.12, 0.0],
            "scenario_C1.json": [3045.54, 942.64, 65.7, 970.36, 1066.84, 0.0],
            "scenario_C2.json": [2792.23, 862.17, 65.7, 887.53, 976.85, 0.0],
            "scenario_C3.json": [1887.55, 574.78, 65.7, 591.68, 655.39, 0.0],
            # "bus_scenario_D.json": [0,0,0,0,0,0],  # buggy: can't charge enough
            "scenario_PV_Bat.json": [-2166.39, 0.0, 65.7, 0.0, 12.48, 2244.58],
        }

        for scenario_name, expected in scenarios.items():
            scen_path = TEST_REPO_PATH.joinpath("test_data/input_test_strategies", scenario_name)
            with scen_path.open() as f:
                j = json.load(f)
            s = scenario.Scenario(j, str(scen_path.parent))
            s.run("greedy", {"cost_calculation": True})
            timeseries = s.GC1_timeseries
            timeseries_lists = [timeseries.get(k, [0] * s.n_intervals) for k in [
                            "time", "grid supply [kW]", "price [EUR/kWh]",
                            "local generation [kW]", "window signal [-]"]]
            price_sheet = TEST_REPO_PATH / 'test_data/input_test_cost_calculation/price_sheet.json'
            pv = sum([pv.nominal_power for pv in s.components.photovoltaics.values()])
            result = cc.calculate_costs("greedy", "MV", s.interval, *timeseries_lists,
                                        s.core_standing_time, str(price_sheet), None, pv)

            for i, value in enumerate(result.values()):
                assert value == expected[i]

    def test_calculate_costs_balanced_A(self):
        scen_path = TEST_REPO_PATH / 'test_data/input_test_strategies/scenario_A.json'
        with scen_path.open() as f:
            j = json.load(f)
        s = scenario.Scenario(j)
        s.run('balanced', {"cost_calculation": True})
        timeseries = s.GC1_timeseries
        timeseries_lists = [timeseries.get(k, [0] * s.n_intervals) for k in [
                            "time", "grid supply [kW]", "price [EUR/kWh]",
                            "local generation [kW]", "window signal [-]"]]
        price_sheet = TEST_REPO_PATH / 'test_data/input_test_cost_calculation/price_sheet.json'

        pv = sum([pv.nominal_power for pv in s.components.photovoltaics.values()])

        # check returned values
        result = cc.calculate_costs("balanced", "MV", s.interval, *timeseries_lists,
                                    s.core_standing_time, str(price_sheet), None, pv)
        assert result["total_costs_per_year"] == 309.4
        assert result["commodity_costs_eur_per_year"] == 73.45
        assert result["capacity_costs_eur"] == 65.7
        assert result["power_procurement_costs_per_year"] == 75.61
        assert result["levies_fees_and_taxes_per_year"] == 94.63
        assert result["feed_in_remuneration_per_year"] == 0

    def test_calculate_costs_balanced_market_A(self):
        scen_path = TEST_REPO_PATH / 'test_data/input_test_strategies/scenario_A.json'
        with scen_path.open() as f:
            j = json.load(f)
        s = scenario.Scenario(j)

        s.run('balanced_market', {"cost_calculation": True})
        timeseries = s.GC1_timeseries
        timeseries_lists = [timeseries.get(k, [0] * s.n_intervals) for k in [
                        "time", "grid supply [kW]", "price [EUR/kWh]",
                        "local generation [kW]", "window signal [-]"]]
        price_sheet = TEST_REPO_PATH / 'test_data/input_test_cost_calculation/price_sheet.json'

        pv = sum([pv.nominal_power for pv in s.components.photovoltaics.values()])

        # check returned values
        result = cc.calculate_costs("balanced_market", "MV", s.interval, *timeseries_lists,
                                    s.core_standing_time, str(price_sheet), None, pv)
        assert result["total_costs_per_year"] == 323.14
        assert result["commodity_costs_eur_per_year"] == 14.41
        assert result["capacity_costs_eur"] == 0
        assert result["power_procurement_costs_per_year"] == 160.88
        assert result["levies_fees_and_taxes_per_year"] == 147.84
        assert result["feed_in_remuneration_per_year"] == 0

    def test_calculate_costs_flex_window_A(self):
        scen_path = TEST_REPO_PATH / 'test_data/input_test_strategies/scenario_A.json'
        with scen_path.open() as f:
            j = json.load(f)
        s = scenario.Scenario(j)
        s.run('flex_window', {"cost_calculation": True})
        timeseries = s.GC1_timeseries
        timeseries_lists = [timeseries.get(k, [0] * s.n_intervals) for k in [
            "time", "grid supply [kW]", "price [EUR/kWh]",
            "local generation [kW]", "window signal [-]"]]
        price_sheet = TEST_REPO_PATH / 'test_data/input_test_cost_calculation/price_sheet.json'

        pv = sum([pv.nominal_power for pv in s.components.photovoltaics.values()])

        # check returned values
        result = cc.calculate_costs("flex_window", "MV", s.interval, *timeseries_lists,
                                    s.core_standing_time, str(price_sheet), None, pv)
        assert result["total_costs_per_year"] == 3932.83
        assert result["commodity_costs_eur_per_year"] == 279.44
        assert result["capacity_costs_eur"] == 1543.08
        assert result["power_procurement_costs_per_year"] == 927.46
        assert result["levies_fees_and_taxes_per_year"] == 1182.84
        assert result["feed_in_remuneration_per_year"] == 0

    def test_calculate_costs_balanced_market_C(self):
        scen_path = TEST_REPO_PATH / 'test_data/input_test_strategies/scenario_C1.json'

        with scen_path.open() as f:
            j = json.load(f)
        s = scenario.Scenario(j, str(scen_path.parent))
        s.run('balanced_market', {"cost_calculation": True})
        timeseries = s.GC1_timeseries
        timeseries_lists = [timeseries.get(k, [0] * s.n_intervals) for k in [
            "time", "grid supply [kW]", "price [EUR/kWh]",
            "local generation [kW]", "window signal [-]"]]
        price_sheet = TEST_REPO_PATH / 'test_data/input_test_cost_calculation/price_sheet.json'

        pv = sum([pv.nominal_power for pv in s.components.photovoltaics.values()])

        # check returned values
        result = cc.calculate_costs("balanced_market", "MV", s.interval, *timeseries_lists,
                                    s.core_standing_time, str(price_sheet), None, pv)
        assert result["total_costs_per_year"] == 495.31
        assert result["commodity_costs_eur_per_year"] == 22.08
        assert result["capacity_costs_eur"] == 0
        assert result["power_procurement_costs_per_year"] == 246.6
        assert result["levies_fees_and_taxes_per_year"] == 226.63
        assert result["feed_in_remuneration_per_year"] == 0


class TestPostSimulationCosts:
    def test_calculate_costs_post_sim(self, tmp_path):
        j = get_test_json()
        s = scenario.Scenario(j)
        save_results = tmp_path / "save_results.json"
        save_timeseries = tmp_path / "save_timeseries.csv"
        price_sheet = TEST_REPO_PATH / 'test_data/input_test_cost_calculation/price_sheet.json'

        s.run("greedy", {
            "save_results": str(save_results),
            "save_timeseries": str(save_timeseries)
        })

        # call calculate cost from shell
        assert subprocess.call([
            "python", TEST_REPO_PATH.parent / "calculate_costs.py",
            "--voltage-level", "MV",
            "--get-results", save_results,
            "--get-timeseries", save_timeseries,
            "--cost-parameters-file", price_sheet
        ]) == 0
        with save_results.open() as f:
            results = json.load(f)
        assert "costs" in results
        assert results["costs"]["electricity costs"]["per year"]["total (gross)"] == 78.18
