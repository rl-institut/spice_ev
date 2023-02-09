import json
from pathlib import Path

from spice_ev import scenario, report


def get_scenario():
    input = Path(__file__).parent / 'test_data/input_test_strategies/scenario_C1.json'
    with input.open('r') as f:
        s = scenario.Scenario(json.load(f), input.parent)
    s.run('greedy', {})
    return s


class TestReport:
    def test_aggregate_global_results(self):
        report.aggregate_global_results(get_scenario())

    def test_aggregate_local_results_simple(self):
        j = {
            "scenario": {
                "start_time": "2020-01-01T00:00:00+02:00",
                "interval": 15,
                "n_intervals": 10
            },
            "components": {
                "grid_connectors": {
                    "GC": {
                        "max_power": 100,
                        "current_loads": {"ext_load": 11},
                        "cost": {"type": "fixed", "value": 0.1},
                    }
                }
            }
        }
        s = scenario.Scenario(j)
        s.run('greedy', {})
        report.generate_reports(s, {"testing": True})
        assert s.testing["avg_drawn_power"]["GC"] == 11

    def test_aggregate_local_results(self):
        s = get_scenario()
        for var in ["avg_drawn", "flex_bands", "total_vehicle_cap", "avg_stand_time",
                    "total_vehicle_energy", "avg_needed_energy", "perc_stand_window",
                    "avg_flex_per_window", "sum_energy_per_window", "avg_total_standing_time"]:
            setattr(s, var, {})
        report.aggregate_local_results(s, 'GC1')

    def test_aggregate_timeseries(self):
        report.aggregate_timeseries(get_scenario(), 'GC1')

    def test_generate_soc_timeseries(self):
        report.generate_soc_timeseries(get_scenario())

    def test_generate_reports(self, tmp_path):
        report.generate_reports(get_scenario(), {
            'save_timeseries': tmp_path / 'timeseries.csv',
            'save_results': tmp_path / 'results.json',
            'save_soc': tmp_path / 'soc.csv',
        })
        assert (tmp_path / 'timeseries.csv').is_file()
        assert (tmp_path / 'results.json').is_file()
        assert (tmp_path / 'soc.csv').is_file()
