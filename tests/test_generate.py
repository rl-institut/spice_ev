import unittest
import argparse
import sys

import generate
import generate_from_csv
import generate_energy_price
import generate_schedule

from src.util import set_options_from_config

sys.argv = ['']
del sys


def create_parser(config_filename):
    parser = argparse.ArgumentParser(
        description='Generate scenarios as JSON files for vehicle charging modelling')
    parser.add_argument('--config', help='Use config file to set arguments',
                        default=config_filename)
    args = parser.parse_args()
    set_options_from_config(args, check=False, verbose=False)
    return args


class TestGenerate(unittest.TestCase):

    def test_generate(self):
        config_filename = "test_data/input_test_generate/generate.cfg"
        args = create_parser(config_filename)
        generate.generate(args)

    def test_generate_from_csv(self):
        config_filename = "test_data/input_test_generate/generate_from_csv.cfg"
        args = create_parser(config_filename)
        generate_from_csv.generate_from_csv(args)

    def test_generate_energy_price(self):
        config_filename = "test_data/input_test_generate/price.cfg"
        args = create_parser(config_filename)
        generate_energy_price.generate_energy_price(args)

    def test_generate_schedule(self):
        config_filename = "test_data/input_test_generate/generate_schedule.cfg"
        args = create_parser(config_filename)
        generate_schedule.generate_schedule(args)


if __name__ == '__main__':
    unittest.main()
