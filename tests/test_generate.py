import unittest
import argparse
import sys
from copy import deepcopy
import os

import generate
import generate_from_csv
import generate_energy_price
import generate_schedule

from src.util import set_options_from_config

sys.argv = ['']
del sys
TEST_REPO_PATH = os.path.dirname(__file__)

ARG_VALUES1 = {
    "--cars": [[1, "golf"], [1, "sprinter"]],
    "--days": 2,
    "--interval": 15,
    "--min_soc": 0.8,
    "--battery": [[350, 0.5]],
    "--start-time": '2018-01-01T00:15:00+00:00',
    "--vehicle_types": "test_data/input_test_generate/vehicle_types.json",
    "--include-ext-load-csv": None,
    "--include-ext-csv-option": [],
    "--include-feed-in-csv": None,
    "--include-feed-in-csv-option": [],
    "--seed": None,
    "--include_price_csv": None,
    "--include_price_csv_option": []
}


def create_parser(arg_values):
    parser = argparse.ArgumentParser(
        description='Generate scenarios as JSON files for vehicle charging modelling')
    for k, v in arg_values.items():
        parser.add_argument(k, nargs='?', default=v)

    args = parser.parse_args()
    set_options_from_config(args, check=False, verbose=False)
    return args, parser


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
        args, parser = create_parser(current_arg_values)
        generate.generate(args, parser)
        self.assertIsFile(output_file)
        # remove output file
        os.remove(output_file)

    def test_generate_from_csv(self):
        output_file = os.path.join(TEST_REPO_PATH,
                                   "test_data/input_test_generate/generate_from_csv.json")
        current_arg_values = deepcopy(ARG_VALUES1)
        current_arg_values.update({"output": output_file})
        current_arg_values.update({
            "input_file":
                os.path.join(TEST_REPO_PATH,
                             "test_data/input_test_generate/rotations_example_table.csv")})
        current_arg_values.update({
            "vehicle_types":
                os.path.join(TEST_REPO_PATH,
                             "test_data/input_test_generate/vehicle_types.json")})
        args, parser = create_parser(current_arg_values)
        generate_from_csv.generate_from_csv(args)
        self.assertIsFile(output_file)
        os.remove(output_file)

    def test_generate_energy_price(self):
        output_file = os.path.join(TEST_REPO_PATH, "test_data/input_test_generate/price.csv")
        current_arg_values = {
            "output": output_file,
            "--start": "2020-12-31T00:00:00+01:00",
            "--n-intervals": 336,
            "--interval": 1,
            "--price_seed": 0,
        }
        args, parser = create_parser(current_arg_values)
        generate_energy_price.generate_energy_price(args)
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
            "--priority_percentile": 0.25,
            "--visual": False,
            "--core_standing_time": {"times": [{"start": [22, 0], "end": [5, 0]}], "full_days": [7]}
        }
        args, parser = create_parser(current_arg_values)
        generate_schedule.generate_schedule(args)
        self.assertIsFile(output_file)
        os.remove(output_file)


if __name__ == '__main__':
    unittest.main()
