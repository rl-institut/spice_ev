"""
This script allows import of multiple SimBEV regions into SpiceEV scenarios.
Author: Moritz
"""

from simulate import simulate
from argparse import Namespace
from pathlib import Path


def simulate_function(input_file, output_file, result_file, strategy="balanced", testing=False):
    """Call simulate with a complete Namespace
    input_file: Path to scenario json as String
    """
    params = Namespace(input=input_file, strategy=strategy, margin=1,
                       strategy_option=[("allow_negative_soc", True)], visual=False, testing=testing,
                       save_timeseries=output_file, save_results=result_file, config=None, eta=False)
    simulate(params)


if __name__ == '__main__':
    # set SpiceEV scenario directory
    dirs = Path("elmobile_data", "elmobile_scenario_1_Jan")
    plz_list = [f.stem for f in dirs.rglob("*.json")]
    # set charging strategy
    strat = "balanced"
    result_dir = Path(dirs, "res_" + strat)
    result_dir.mkdir(exist_ok=True)
    for counter, plz in enumerate(plz_list):
        simulate_function(Path(dirs, plz + ".json"), str(Path(result_dir, plz + ".csv")),
                          str(Path(result_dir, plz + ".json")), strategy=strat)
        print("Region {} done! ({}/{})".format(plz, counter+1, len(plz_list)))
