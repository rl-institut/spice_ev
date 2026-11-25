from copy import deepcopy
import json
from pathlib import Path
import pytest

from spice_ev import oemof_objective, scenario

TEST_REPO_PATH = Path(__file__).parent
PRICE_SHEET_PATH = TEST_REPO_PATH / "test_data/input_test_cost_calculation/price_sheet.json"

TEMPLATE = {
    "electricity_costs_method": {"value": "fixed_wo_plw", "info": "description"},
    "energy_charges": {"weight": 1, "info": "description"},
    "capacity_charge": {"weight": 1},
    "grid_connection": {"weight": 1},
    "feed_in_remuneration": {"weight": 1},
    "stationary_storage_sizing": {
        "optimize_capacity": False, "weight": 1,
        "min_capacity_kWh": 0, "max_capacity_kWh": None},
    "stationary_storage_balanced": {"value": True},
}


def write_objective(tmp_path, objective):
    path = tmp_path / "oemof_objective.json"
    with open(path, "w") as f:
        json.dump(objective, f)
    return path


def load_price_sheet():
    with open(PRICE_SHEET_PATH) as f:
        return json.load(f)


def get_two_gc_scenario(n_days=1, soc=0.9, desired_soc=0.5):
    # one vehicle standing at GC_A (MV), then GC_B (LV), then GC_A again
    # signal prices favor GC_B, cost file favors GC_A (MV commodity charge is lower)
    def t(hour):
        return f"2020-01-01T{hour:02d}:00:00+01:00"
    gcs = {"GC_A": ("MV", 1.0), "GC_B": ("LV", 0.01)}
    return {
        "scenario": {"start_time": t(0), "interval": 15, "n_intervals": 96 * n_days},
        "components": {
            "grid_connectors": {gc_id: {
                "max_power": 200, "voltage_level": vl, "cost": {"type": "fixed", "value": cost}}
                for gc_id, (vl, cost) in gcs.items()},
            "charging_stations": {
                "CS_A": {"max_power": 50, "min_power": 0, "parent": "GC_A"},
                "CS_B": {"max_power": 50, "min_power": 0, "parent": "GC_B"}},
            "vehicle_types": {"bus": {
                "name": "bus", "capacity": 100, "mileage": 100,
                "charging_curve": [[0, 50], [1, 50]], "min_charging_power": 0, "v2g": False}},
            "vehicles": {"bus_0": {
                "vehicle_type": "bus", "soc": soc, "desired_soc": desired_soc,
                "connected_charging_station": "CS_A", "estimated_time_of_departure": t(6)}},
        },
        "events": {
            # fixed loads: annual energy above SLP limit -> RLM
            "fixed_load": {f"load_{gc_id}": {
                "start_time": t(0), "step_duration_s": 3600, "grid_connector_id": gc_id,
                "values": [20] * 24 * n_days} for gc_id in gcs},
            "grid_operator_signals": [],
            "vehicle_events": [
                {"signal_time": t(6), "start_time": t(6), "vehicle_id": "bus_0",
                 "event_type": "departure", "update": {}},
                {"signal_time": t(8), "start_time": t(8), "vehicle_id": "bus_0",
                 "event_type": "arrival", "update": {
                     "connected_charging_station": "CS_B", "soc_delta": -0.3,
                     "estimated_time_of_departure": t(12), "desired_soc": desired_soc}},
                {"signal_time": t(12), "start_time": t(12), "vehicle_id": "bus_0",
                 "event_type": "departure", "update": {}},
                {"signal_time": t(14), "start_time": t(14), "vehicle_id": "bus_0",
                 "event_type": "arrival", "update": {
                     "connected_charging_station": "CS_A", "soc_delta": -0.3,
                     "estimated_time_of_departure": t(23), "desired_soc": desired_soc}},
            ],
        },
    }


class TestOemofSolphObjective:
    def run(self, tmp_path, objective, options=None, **scenario_args):
        pytest.importorskip("oemof.solph")
        s = scenario.Scenario(get_two_gc_scenario(**scenario_args), tmp_path)
        s.run("oemof_solph", dict({
            "oemof_objective": write_objective(tmp_path, objective),
            "cost_parameters_file": PRICE_SHEET_PATH,
        }, **(options or dict())))
        assert s.step_i == s.n_intervals
        self.scenario = s
        # energy for vehicle per GC (fixed load subtracted)
        return {gc_id: sum(loads) / 4 - 20 * 24 for gc_id, loads in s.totalLoad.items()}

    def test_lp_uses_rates_from_cost_file(self, tmp_path):
        energy = self.run(tmp_path, TEMPLATE)
        # charging at GC_A (cheaper in cost file), not GC_B (cheaper signal price)
        assert energy["GC_A"] > 15
        assert energy["GC_B"] == pytest.approx(0, abs=0.1)

    def test_weight_zero_removes_energy_charges(self, tmp_path):
        objective = deepcopy(TEMPLATE)
        objective["gc_overrides"] = [{"gcs": ["GC_B"], "energy_charges": {"weight": 0}}]
        energy = self.run(tmp_path, objective)
        # energy at GC_B is free now
        assert energy["GC_A"] == pytest.approx(0, abs=0.1)
        assert energy["GC_B"] > 15

    def test_desired_soc_reached_despite_high_costs(self, tmp_path):
        # long simulation, high per-kW costs: penalty must still dominate
        n_days = 7
        self.run(tmp_path, TEMPLATE, {"PEAK_POWER_COST": 1000},
                 n_days=n_days, soc=0.5, desired_soc=0.8)
        socs = self.scenario.socs
        # SoC at departures from GC_A (6:00), GC_B (12:00) and GC_A (estimated, 23:00)
        for step in [24, 48, 92]:
            assert socs[step][0] > 0.8 - 1e-3

    def test_missing_objective_aborts(self, tmp_path):
        pytest.importorskip("oemof.solph")
        s = scenario.Scenario(get_two_gc_scenario(), tmp_path)
        with pytest.raises(Exception, match="oemof_objective"):
            s.run("oemof_solph", {"cost_parameters_file": PRICE_SHEET_PATH})


class TestOemofSolphV2G:
    def test_discharge_limited_by_charging_curve(self, tmp_path, monkeypatch):
        # V2G vehicle (10 kW charging curve) at 50 kW station covers a 60 kW load peak
        pytest.importorskip("oemof.solph")
        from spice_ev.strategies import oemof_solph

        def t(hour):
            return f"2020-01-01T{hour:02d}:00:00+01:00"
        scenario_json = {
            "scenario": {"start_time": t(0), "interval": 15, "n_intervals": 96},
            "components": {
                "grid_connectors": {"GC1": {
                    "max_power": 200, "voltage_level": "MV",
                    "cost": {"type": "fixed", "value": 0.3}}},
                "charging_stations": {"CS1": {"max_power": 50, "min_power": 0, "parent": "GC1"}},
                "vehicle_types": {"bus": {
                    "name": "bus", "capacity": 100, "mileage": 100,
                    "charging_curve": [[0, 10], [1, 10]], "min_charging_power": 0,
                    "v2g": True}},
                "vehicles": {"bus_0": {
                    "vehicle_type": "bus", "soc": 0.9, "desired_soc": 0.1,
                    "connected_charging_station": "CS1", "estimated_time_of_departure": t(23)}},
            },
            "events": {
                "fixed_load": {"load_GC1": {
                    "start_time": t(0), "step_duration_s": 3600, "grid_connector_id": "GC1",
                    "values": [0] * 12 + [60] + [0] * 11}},
                "grid_operator_signals": [],
                "vehicle_events": [],
            },
        }
        # keep strategy instance to read planned SoC
        strategies = []

        class RecordingOemofSolph(oemof_solph.OemofSolph):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                strategies.append(self)
        monkeypatch.setattr(
            scenario.strategy, "class_from_str", lambda name: RecordingOemofSolph)

        s = scenario.Scenario(scenario_json, tmp_path)
        s.run("oemof_solph", {
            "oemof_objective": write_objective(tmp_path, TEMPLATE),
            "cost_parameters_file": PRICE_SHEET_PATH,
        })
        planned_soc = strategies[0].batteries["vehicle"]["bus_0"]
        max_soc_drop = max(a - b for a, b in zip(planned_soc, planned_soc[1:]))
        # 10 kW at GC for 15 min, battery losses included
        efficiency = s.components.vehicles["bus_0"].battery.efficiency
        limit = 10 / efficiency / 4 / 100
        assert max_soc_drop > 0.5 * limit
        assert max_soc_drop <= limit + 1e-6


class TestGetGcRates:
    # expected net energy prices from test price sheet in ct/kWh: commodity + procurement 7.7
    # + levies 1.237 + concession fee 1.32 + electricity tax 2.05 (no VAT)
    def test_rlm_by_voltage_level(self):
        price_sheet = load_price_sheet()
        for voltage_level, price in [("MV", 14.627), ("LV", 16.337)]:
            rates = oemof_objective.get_gc_rates(
                price_sheet, "GC1", "default_grid_operator", voltage_level, 2e5, 1)
            assert rates["fee_type"] == "RLM"
            assert rates["energy_price"] == pytest.approx(price / 100)

    def test_slp_below_annual_energy_limit(self):
        rates = oemof_objective.get_gc_rates(
            load_price_sheet(), "GC1", "default_grid_operator", "LV", 1e5, 1)
        assert rates["fee_type"] == "SLP"
        assert rates["energy_price"] == pytest.approx(0.19787)

    def test_fallbacks(self):
        with pytest.warns(UserWarning, match="MV"):
            rates = oemof_objective.get_gc_rates(load_price_sheet(), "GC1", None, None, 2e5, 1)
        assert rates["grid_operator"] == "default_grid_operator"
        assert rates["voltage_level"] == "MV"
        assert rates["energy_price"] == pytest.approx(0.14627)

    def test_missing_cost_entries_abort_if_weighted(self):
        price_sheet = load_price_sheet()
        for operator, voltage_level, missing in [
                ("unknown_operator", "MV", "unknown_operator"),
                ("default_grid_operator", "XV", "XV")]:
            with pytest.raises(Exception, match=f"GC1.*{missing}"):
                oemof_objective.get_gc_rates(price_sheet, "GC1", operator, voltage_level, 2e5, 1)
            # weight 0: rate not needed
            rates = oemof_objective.get_gc_rates(
                price_sheet, "GC1", operator, voltage_level, 2e5, 0)
            assert rates["energy_price"] == 0
        # other missing entries: clear message
        del price_sheet["default_grid_operator"]["concession_fee"]
        with pytest.raises(Exception, match="GC1.*default_grid_operator.*concession_fee"):
            oemof_objective.get_gc_rates(price_sheet, "GC1", "default_grid_operator", "MV", 2e5, 1)
        # SLP does not depend on voltage level
        price_sheet = load_price_sheet()
        oemof_objective.get_gc_rates(price_sheet, "GC1", "default_grid_operator", "XV", 1e3, 1)


class TestReadObjective:
    def test_missing_file_aborts(self, tmp_path):
        for path in [None, tmp_path / "missing.json"]:
            with pytest.raises(Exception, match="oemof_objective"):
                oemof_objective.read_objective(path, ["GC1"])

    def test_template_is_valid(self, tmp_path):
        settings = oemof_objective.read_objective(write_objective(tmp_path, TEMPLATE), ["GC1"])
        assert settings["GC1"]["electricity_costs_method"]["value"] == "fixed_wo_plw"
        assert settings["GC1"]["energy_charges"]["weight"] == 1

    def test_missing_entries_abort_and_are_listed(self, tmp_path):
        objective = deepcopy(TEMPLATE)
        del objective["grid_connection"]
        del objective["stationary_storage_sizing"]["max_capacity_kWh"]
        del objective["electricity_costs_method"]["value"]
        path = write_objective(tmp_path, objective)
        with pytest.raises(Exception) as e:
            oemof_objective.read_objective(path, ["GC1"])
        msg = str(e.value)
        assert "grid_connection" in msg
        assert "stationary_storage_sizing.max_capacity_kWh" in msg
        assert "electricity_costs_method.value" in msg
        # gc_overrides is optional, info is optional
        assert "gc_overrides" not in msg
        assert "info" not in msg

    def test_unknown_entries_warn(self, tmp_path):
        objective = deepcopy(TEMPLATE)
        objective["grid_conection"] = {"weight": 1}
        objective["energy_charges"]["wieght"] = 2
        objective["gc_overrides"] = [
            {"gcs": ["GC1"], "capacity_chrge": {"weight": 0}},
            {"gcs": ["GC1"], "capacity_charge": {"weigth": 0}},
        ]
        path = write_objective(tmp_path, objective)
        with pytest.warns(UserWarning) as record:
            oemof_objective.read_objective(path, ["GC1"])
        msg = " ".join(str(w.message) for w in record)
        for unknown in ["grid_conection", "energy_charges.wieght", "capacity_chrge",
                        "capacity_charge.weigth"]:
            assert unknown in msg

    def test_gc_overrides(self, tmp_path, caplog):
        objective = deepcopy(TEMPLATE)
        objective["gc_overrides"] = [
            {"gcs": ["A", "C"], "stationary_storage_sizing": {
                "optimize_capacity": True, "max_capacity_kWh": 5000}},
            {"gcs": ["C", "X"], "electricity_costs_method": {"value": "fixed_w_plw"},
             "stationary_storage_sizing": {"max_capacity_kWh": 100}},
        ]
        path = write_objective(tmp_path, objective)
        caplog.set_level("INFO")
        with pytest.warns(UserWarning, match="X"):
            settings = oemof_objective.read_objective(path, ["A", "B", "C"])
        assert set(settings) == {"A", "B", "C"}
        # B: global settings
        assert settings["B"]["stationary_storage_sizing"] == {
            "optimize_capacity": False, "weight": 1,
            "min_capacity_kWh": 0, "max_capacity_kWh": None}
        # A: partial override, weight stays global
        assert settings["A"]["stationary_storage_sizing"] == {
            "optimize_capacity": True, "weight": 1,
            "min_capacity_kWh": 0, "max_capacity_kWh": 5000}
        assert settings["A"]["electricity_costs_method"]["value"] == "fixed_wo_plw"
        # C: both rules, later rule wins on same field
        assert settings["C"]["stationary_storage_sizing"] == {
            "optimize_capacity": True, "weight": 1,
            "min_capacity_kWh": 0, "max_capacity_kWh": 100}
        assert settings["C"]["electricity_costs_method"]["value"] == "fixed_w_plw"
        # effective settings logged per GC
        for gc_id in "ABC":
            assert any(f"GC {gc_id}" in r.message for r in caplog.records)

    def test_entries_must_be_objects(self, tmp_path):
        for key, value in [("energy_charges", 1), ("gc_overrides", ["GC1"]),
                           ("gc_overrides", [{"gcs": ["GC1"], "energy_charges": 0}])]:
            objective = deepcopy(TEMPLATE)
            objective[key] = value
            with pytest.raises(ValueError):
                oemof_objective.read_objective(write_objective(tmp_path, objective), ["GC1"])

    @pytest.mark.parametrize("entry, field, value", [
        ("electricity_costs_method", "value", "fixed"),
        ("energy_charges", "weight", -1),
        ("capacity_charge", "weight", "1"),
        ("stationary_storage_sizing", "optimize_capacity", "yes"),
        ("stationary_storage_sizing", "min_capacity_kWh", -5),
        ("stationary_storage_sizing", "max_capacity_kWh", -5),
        ("stationary_storage_balanced", "value", 1),
    ])
    def test_invalid_values_abort(self, tmp_path, entry, field, value):
        objective = deepcopy(TEMPLATE)
        objective[entry][field] = value
        with pytest.raises(ValueError, match=f"{entry}.{field}"):
            oemof_objective.read_objective(write_objective(tmp_path, objective), ["GC1"])
        # same in gc_overrides
        objective = deepcopy(TEMPLATE)
        objective["gc_overrides"] = [{"gcs": ["GC1"], entry: {field: value}}]
        with pytest.raises(ValueError, match=f"{entry}.{field}"):
            oemof_objective.read_objective(write_objective(tmp_path, objective), ["GC1"])
