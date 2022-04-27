import unittest
import sys
from copy import deepcopy
import os
from argparse import Namespace

import generate
import generate_from_csv
import generate_energy_price
import generate_schedule

sys.argv = ['']
del sys
TEST_REPO_PATH = os.path.dirname(__file__)

ARG_VALUES1 = {
    "cars": [[1, "golf"], [1, "sprinter"]],
    "days": 2,
    "interval": 15,
    "min_soc": 0.8,
    "battery": [[350, 0.5]],
    "start_time": '2018-01-01T00:15:00+00:00',
    "no_drive_days": [6],
    "vehicle_types": "test_data/input_test_generate/vehicle_types.json",
    "discharge_limit": 0.5,
    "include_ext_load_csv": None,
    "include_ext_csv_option": [],
    "include_feed_in_csv": None,
    "include_feed_in_csv-option": [],
    "seed": None,
    "include_price_csv": None,
    "include_price_csv_option": []
}


class TestCaseBase(unittest.TestCase):
    def assertIsFile(self, path):
        if not os.path.isfile(path):
            raise AssertionError("File does not exist: %s" % str(path))


class TestGenerate(TestCaseBase):

    def test_generate(self):
        output_file = os.path.join(TEST_REPO_PATH, "test_data/input_test_generate/generate.json")
        current_arg_values = deepcopy(ARG_VALUES1)
        current_arg_values.update({"output": output_file})
        current_arg_values.update({
            "vehicle_types":
                os.path.join(TEST_REPO_PATH,
                             "test_data/input_test_generate/vehicle_types.json")})
        current_arg_values.update({
            "discharge_limit": 0.5})
        generate.generate(Namespace(**current_arg_values))
        self.assertIsFile(output_file)
        # remove output file
        os.remove(output_file)

    def test_generate_from_csv_1_soc(self):
        input_csv = "test_data/input_test_generate/generate_from_csv_template1.csv"
        output_file = os.path.join(TEST_REPO_PATH,
                                   "test_data/input_test_generate/generate_from_csv.json")
        current_arg_values = deepcopy(ARG_VALUES1)
        current_arg_values.update({"output": output_file})
        current_arg_values.update({
            "input_file":
                os.path.join(TEST_REPO_PATH, input_csv)})
        current_arg_values.update({
            "vehicle_types":
                os.path.join(TEST_REPO_PATH,
                             "test_data/input_test_generate/vehicle_types.json"),
            "export_vehicle_id_csv": None})
        generate_from_csv.generate_from_csv(Namespace(**current_arg_values))
        self.assertIsFile(output_file)
        os.remove(output_file)

    def test_generate_from_csv_2_delta_soc(self):
        input_csv = "test_data/input_test_generate/generate_from_csv_template2.csv"
        output_file = os.path.join(TEST_REPO_PATH,
                                   "test_data/input_test_generate/generate_from_csv.json")
        current_arg_values = deepcopy(ARG_VALUES1)
        current_arg_values.update({"output": output_file})
        current_arg_values.update({
            "input_file":
                os.path.join(TEST_REPO_PATH, input_csv)})
        current_arg_values.update({
            "vehicle_types":
                os.path.join(TEST_REPO_PATH,
                             "test_data/input_test_generate/vehicle_types.json"),
            "export_vehicle_id_csv": None})
        generate_from_csv.generate_from_csv(Namespace(**current_arg_values))
        self.assertIsFile(output_file)
        os.remove(output_file)

    def test_generate_from_csv_3_distance(self):
        input_csv = "test_data/input_test_generate/generate_from_csv_template3.csv"
        output_file = os.path.join(TEST_REPO_PATH,
                                   "test_data/input_test_generate/generate_from_csv.json")
        current_arg_values = deepcopy(ARG_VALUES1)
        current_arg_values.update({"output": output_file})
        current_arg_values.update({
            "input_file": os.path.join(TEST_REPO_PATH, input_csv),
            "vehicle_types": os.path.join(TEST_REPO_PATH,
                                          "test_data/input_test_generate/vehicle_types.json"),
            "export_vehicle_id_csv": None})
        generate_from_csv.generate_from_csv(Namespace(**current_arg_values))
        self.assertIsFile(output_file)
        os.remove(output_file)

    def test_generate_from_csv_4_vehicle_id(self):
        input_csv = "test_data/input_test_generate/generate_from_csv_template4.csv"
        output_file = os.path.join(TEST_REPO_PATH,
                                   "test_data/input_test_generate/generate_from_csv.json")
        current_arg_values = deepcopy(ARG_VALUES1)
        current_arg_values.update(
            {"output": output_file,
             "input_file": os.path.join(TEST_REPO_PATH, input_csv),
             "vehicle_types": os.path.join(TEST_REPO_PATH,
                                           "test_data/input_test_generate/vehicle_types.json"),
             "recharge_fraction": 1,
             "export_vehicle_id_csv": None})
        generate_from_csv.generate_from_csv(Namespace(**current_arg_values))
        self.assertIsFile(output_file)
        os.remove(output_file)

    def test_generate_from_csv_5_min_standing_time(self):
        input_csv = "test_data/input_test_generate/generate_from_csv_template4.csv"
        output_file = os.path.join(TEST_REPO_PATH,
                                   "test_data/input_test_generate/generate_from_csv.json")
        vehicle_id_file = "vehicle_id.csv"
        current_arg_values = deepcopy(ARG_VALUES1)
        current_arg_values.update(
            {"output": output_file,
             "input_file": os.path.join(TEST_REPO_PATH, input_csv),
             "vehicle_types": os.path.join(TEST_REPO_PATH,
                                           "test_data/input_test_generate/vehicle_types.json"),
             "export_vehicle_id_csv": vehicle_id_file})
        generate_from_csv.generate_from_csv(Namespace(**current_arg_values))
        self.assertIsFile(output_file)
        target_path = os.path.dirname(output_file)
        self.assertIsFile(os.path.join(target_path, vehicle_id_file))
        os.remove(output_file)
        os.remove(os.path.join(target_path, vehicle_id_file))

    def test_generate_energy_price(self):
        output_file = os.path.join(TEST_REPO_PATH, "test_data/input_test_generate/price.csv")
        current_arg_values = {
            "output": output_file,
            "start": "2020-12-31T00:00:00+01:00",
            "n_intervals": 336,
            "interval": 1,
            "price_seed": 0,
        }
        generate_energy_price.generate_energy_price(Namespace(**current_arg_values))
        self.assertIsFile(output_file)
        os.remove(output_file)

    def test_generate_schedule(self):
        output_file = os.path.join(TEST_REPO_PATH,
                                   "test_data/input_test_generate/schedule_example.csv")
        current_arg_values = {
            "input": os.path.join(TEST_REPO_PATH, "test_data/input_test_generate/nsm_00_dummy.csv"),
            "scenario": os.path.join(TEST_REPO_PATH,
                                     "test_data/input_test_generate/scenario_C.json"),
            "output": output_file,
            "priority_percentile": 0.25,
            "visual": False,
            "core_standing_time": {"times": [{"start": [22, 0], "end": [5, 0]}], "full_days": [7]}
        }
        generate_schedule.generate_schedule(Namespace(**current_arg_values))
        self.assertIsFile(output_file)
        os.remove(output_file)


if __name__ == '__main__':
    unittest.main()
