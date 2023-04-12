from argparse import Namespace
import json
from pathlib import Path
import pytest
import warnings

from generate import generate
from spice_ev import scenario
from spice_ev.generate import generate_schedule

TEST_REPO_PATH = Path(__file__).parent

ARG_VALUES1 = {
    "vehicles": [[1, "golf"], [1, "sprinter"]],
    "days": 2,
    "interval": 15,
    "min_soc": 0.8,
    "min_soc_threshold": 0.05,
    "battery": [[350, 0.5]],
    "start_time": '2023-01-01T00:15:00+00:00',
    "no_drive_days": [6],
    "vehicle_types": str(TEST_REPO_PATH / "test_data/input_test_generate/vehicle_types.json"),
    "discharge_limit": 0.5,
    "cs_power_min": 0,
    "export_vehicle_id_csv": None,
    "seed": None,
    "verbose": 0,
    "voltage_level": "MV",
    # generate_schedule
    "core_standing_time": None,
    "visual": False,
}


class TestCaseBase:

    def assertIsFile(self, path):
        assert path.exists()
        assert path.is_file()


class TestGenerate(TestCaseBase):

    def test_generate_from_statistics(self, tmp_path):
        current_arg_values = ARG_VALUES1.copy()
        current_arg_values.update({"mode": "statistics", "output": tmp_path / "generate.json"})
        generate(Namespace(**current_arg_values))
        self.assertIsFile(tmp_path / "generate.json")
        # try to create scenario from generated file
        with open(tmp_path / "generate.json") as f:
            j = json.load(f)
            scenario.Scenario(j)

    def test_generate_from_statistics_external_files(self, tmp_path):
        output_file = tmp_path / "generate.json"
        current_arg_values = ARG_VALUES1.copy()
        current_arg_values.update({
            "mode": "statistics",
            "output": output_file,
            "include_fixed_load_csv": str(
                TEST_REPO_PATH / "test_data/input_test_generate/example_load.csv"),
            "include_fixed_load_csv_option": [("column", "value"), ("factor", 0.0001)],
            "include_local_generation_csv": str(
                TEST_REPO_PATH / "test_data/input_test_generate/example_pv_feedin.csv"),
            "include_local_generation_csv_option": [
                ("column", "Feed-in Total (kW)"), ("step_duration_s", 60)],
            "include_price_csv": str(
                TEST_REPO_PATH / "test_data/input_test_generate/example_load.csv"),
            "include_price_csv_option": [("column", "value")],
        })
        generate(Namespace(**current_arg_values))
        self.assertIsFile(tmp_path / "generate.json")
        # try to create scenario from generated file
        with open(output_file) as f:
            j = json.load(f)
            s = scenario.Scenario(j)
            s.n_intervals = 5
            s.run("greedy", {})
            assert sum(s.localGenerationPower["GC1"]) != 0
            assert pytest.approx(sum(s.fixedLoads["GC1"][-1].values())) == -33
            assert s.prices["GC1"][-2] == 11319.32
            assert pytest.approx(s.prices["GC1"][-1]) == 11585.256

    def test_generate_from_csv_1_soc(self, tmp_path):
        input_csv = "test_data/input_test_generate/generate_from_csv_template1.csv"
        output_file = tmp_path / "generate_from_csv.json"
        current_arg_values = ARG_VALUES1.copy()
        current_arg_values.update({
            "mode": "csv",
            "input_file": TEST_REPO_PATH / input_csv,
            "output": output_file,
        })
        generate(Namespace(**current_arg_values))
        self.assertIsFile(output_file)
        # try to create scenario from generated file
        with open(output_file) as f:
            j = json.load(f)
            scenario.Scenario(j)

    def test_generate_from_csv_2_delta_soc(self, tmp_path):
        input_csv = "test_data/input_test_generate/generate_from_csv_template2.csv"
        output_file = tmp_path / "generate_from_csv.json"
        current_arg_values = ARG_VALUES1.copy()
        current_arg_values.update({
            "mode": "csv",
            "input_file": TEST_REPO_PATH / input_csv,
            "output": output_file,
        })
        generate(Namespace(**current_arg_values))
        self.assertIsFile(output_file)

    def test_generate_from_csv_3_distance(self, tmp_path):
        input_csv = "test_data/input_test_generate/generate_from_csv_template3.csv"
        output_file = tmp_path / "generate_from_csv.json"
        current_arg_values = ARG_VALUES1.copy()
        current_arg_values.update({
            "mode": "csv",
            "input_file": TEST_REPO_PATH / input_csv,
            "output": output_file,
        })
        generate(Namespace(**current_arg_values))
        self.assertIsFile(output_file)

    def test_generate_from_csv_4_vehicle_id(self, tmp_path):
        input_csv = "test_data/input_test_generate/generate_from_csv_template4.csv"
        output_file = tmp_path / "generate_from_csv.json"
        current_arg_values = ARG_VALUES1.copy()
        current_arg_values.update({
            "mode": "csv",
            "input_file": TEST_REPO_PATH / input_csv,
            "output": output_file,
        })
        generate(Namespace(**current_arg_values))
        self.assertIsFile(output_file)

    def test_generate_from_csv_5_min_standing_time(self, tmp_path):
        input_csv = "test_data/input_test_generate/generate_from_csv_template4.csv"
        output_file = tmp_path / "generate_from_csv.json"
        vehicle_id_file = tmp_path / "vehicle_id.csv"
        current_arg_values = ARG_VALUES1.copy()
        current_arg_values.update({
            "mode": "csv",
            "input_file": TEST_REPO_PATH / input_csv,
            "output": tmp_path / "generate_from_csv.json",
            "export_vehicle_id_csv": vehicle_id_file,
        })
        generate(Namespace(**current_arg_values))
        self.assertIsFile(output_file)
        self.assertIsFile(vehicle_id_file)

    def test_generate_from_simbev(self, tmp_path):
        simbev_dir = TEST_REPO_PATH / "../examples/example_simbev_run"
        output_file = tmp_path / "generate_from_simbev.json"
        current_arg_values = ARG_VALUES1.copy()
        current_arg_values.update({
            "mode": "simbev",
            "simbev": simbev_dir,
            "output": str(output_file),
            "region": None,
            "vehicle_types": None,
            "ignore_simbev_soc": False,
        })
        generate(Namespace(**current_arg_values))
        self.assertIsFile(output_file)
        # try to create scenario from generated file
        with open(output_file) as f:
            j = json.load(f)
            scenario.Scenario(j)

    def test_generate_from_simbev_ignore_soc(self, tmp_path):
        simbev_dir = TEST_REPO_PATH / "../examples/example_simbev_run"
        output_file = tmp_path / "generate_from_simbev.json"
        current_arg_values = ARG_VALUES1.copy()
        current_arg_values.update({
            "mode": "simbev",
            "simbev": simbev_dir,
            "output": str(output_file),
            "region": "region_1",
            "vehicle_types": None,
            "ignore_simbev_soc": True,
        })
        generate(Namespace(**current_arg_values))
        self.assertIsFile(output_file)

    def test_generate_update_namespace(self):
        # tests various more obscure options not covered by other tests
        with pytest.raises(SystemExit):
            # required options missing
            generate(Namespace())

        args = ARG_VALUES1.copy()
        args.update({
            # ignore output
            "output": "/dev/null",
            # no vehicle_types given
            "vehicle_types": None,
            # no voltage_level given
            "voltage_level": None,
            # pv_power given
            "pv_power": 100,
            # unlimited battery
            "battery": [(-1, 1)],
            # CSV file does not exist
            "include_ext_load_csv": "DOES NOT EXIST",
            # wrong column
            "include_price_csv": str(TEST_REPO_PATH / "../examples/data/price_sheet.json"),
            "include_price_csv_option": [("column", "DOES NOT EXIST")],
            # CSV options without file
            "include_feed_in_csv_option": [("grid_connector_id", "GC")],
        })
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            generate(Namespace(**args))


class TestGenerateSchedule(TestCaseBase):
    def test_generate_flex_collective(self):
        inp_file = TEST_REPO_PATH / "test_data/input_test_generate/generate_schedule_2vehicles.json"
        with inp_file.open('r') as f:
            scenario_json = json.load(f)
            s = scenario.Scenario(scenario_json, TEST_REPO_PATH)
        flex = generate_schedule.generate_flex_band(s, "GC1")

        assert not any(flex["min"] + flex["base"])  # all 0
        assert flex["max"] == ([44]*32 + [22]*4 + [0]*22 + [22]*8 + [44]*62 +
                               [22]*2 + [0]*23 + [22]*6 + [44]*33)
        energy_needed = [0, 250, 250]
        timesteps = [(0, 35), (58, 129), (153, 191)]
        vehicles = [1, 1, 2]
        for i, interval in enumerate(flex["intervals"]):
            assert interval["needed"] == energy_needed[i]
            assert (interval["time"][0], interval["time"][-1]) == timesteps[i]
            assert interval["num_vehicles_present"] == vehicles[i]

    def test_generate_collective(self, tmp_path):
        # copy scenario to tmp
        input_json = "generate_schedule_2vehicles.json"
        src = TEST_REPO_PATH / "test_data/input_test_generate" / input_json
        dst = tmp_path / input_json
        dst.write_text(src.read_text())
        schedule_file = tmp_path / "schedule.json"
        current_arg_values = ARG_VALUES1.copy()
        current_arg_values.update({
            "input": TEST_REPO_PATH / "test_data/input_test_generate/grid_situation_2vehicles.csv",
            "scenario": str(dst),
            "output": str(schedule_file),
            "individual": False,
        })
        generate_schedule.generate_schedule(Namespace(**current_arg_values))
        with dst.open('r') as f:
            j = json.load(f)
            assert j["events"]["schedule_from_csv"]["csv_file"] == "schedule.json"

        with schedule_file.open('r') as f:
            # skip header
            next(f)
            schedule = [float(row.split(',')[1]) for row in f]

        assert len(schedule) == 192
        assert sum(schedule[:58]) == 0
        assert pytest.approx(sum(schedule[59:129]), 0.1) == 1000
        assert sum(schedule[130:153]) == 0
        assert pytest.approx(sum(schedule[154:]), 0.1) == 1000

    def test_generate_flex_individual(self):
        inp_file = TEST_REPO_PATH / "test_data/input_test_generate/generate_schedule_2vehicles.json"
        with inp_file.open('r') as f:
            scenario_json = json.load(f)
            s = scenario.Scenario(scenario_json, TEST_REPO_PATH)
        flex = generate_schedule.generate_individual_flex_band(s, "GC1")

        assert flex["min"] == [-50]*192
        assert flex["base"] == [0]*192
        assert flex["max"] == [50]*192
        assert len(flex["vehicles"]) == 192
        assert sum(map(bool, flex["vehicles"])) == 5  # start + 4 arrival events

        # check first and last vehicle event
        v1 = flex["vehicles"][0]
        assert len(v1) == 2  # 2 vehicles
        assert v1[0]["idx_end"] == 36
        assert v1[0]["energy"] == 0
        assert v1[1]["idx_end"] == 32
        assert v1[1]["energy"] == 0

        v2 = flex["vehicles"][159][0]
        assert v2["idx_end"] == 191
        assert v2["energy"] == 125

    def test_generate_individual(self, tmp_path):
        # copy scenario to tmp
        input_json = "generate_schedule_2vehicles.json"
        src = TEST_REPO_PATH / "test_data/input_test_generate" / input_json
        dst = tmp_path / input_json
        dst.write_text(src.read_text())
        schedule_file = tmp_path / "schedule.json"
        current_arg_values = ARG_VALUES1.copy()
        current_arg_values.update({
            "input": TEST_REPO_PATH / "test_data/input_test_generate/grid_situation_2vehicles.csv",
            "scenario": dst,
            "output": str(schedule_file),
            "individual": True,
        })
        generate_schedule.generate_schedule(Namespace(**current_arg_values))
        with dst.open('r') as f:
            j = json.load(f)
            assert j["events"]["schedule_from_csv"]["csv_file"] == "schedule.json"

        with schedule_file.open('r') as f:
            # skip header
            next(f)
            schedules = [list(map(float, row.split(',')[1:])) for row in f]
        for row in schedules:
            # each timestep schedule must be sum of vehicle schedules
            assert pytest.approx(row[0], 0.01) == sum(row[-2:])

        # transpose schedule
        schedules = list(zip(*schedules))
        assert sum(schedules[0][:58]) == 0
        assert pytest.approx(sum(schedules[0][58:130]), .1) == 1000
        assert sum(schedules[0][130:153]) == 0
        assert pytest.approx(sum(schedules[0][153:191]), .1) == 1000

    def test_generate_complex_schedule(self, tmp_path):
        # slightly more complex scenario with fixed load and local generation
        # copy scenario and needed files to tmp
        path = TEST_REPO_PATH / "test_data/input_test_generate"
        for filename in ["scenario_C.json", "example_load.csv", "example_pv_feedin.csv"]:
            (tmp_path / filename).write_text((path / filename).read_text())
        schedule_file = tmp_path / "schedule.json"
        current_arg_values = {
            "input": path / "example_grid_situation.csv",
            "scenario": tmp_path / "scenario_C.json",
            "output": schedule_file,
            "visual": False,
            "core_standing_time": {"times": [{"start": [22, 0], "end": [5, 0]}], "full_days": [7]},
            "individual": False,
        }
        generate_schedule.generate_schedule(Namespace(**current_arg_values))
        self.assertIsFile(schedule_file)
