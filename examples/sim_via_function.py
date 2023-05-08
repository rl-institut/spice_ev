# This script is an example on how to build a Namespace object that's required by the simulate function
# To run, one of the following conditions must be met:
# - this script is located in the main spice_ev Folder
# - spice_ev is added to the pythonpath (see next lines on how to do this automatically)

# automatically add spice_ev to pythonpath, given this file is in examples
from pathlib import Path
import sys
root_dir = Path(__file__).parent.parent.absolute()
if str(root_dir) not in sys.path:
    sys.path.append(str(root_dir))

from simulate import simulate
from argparse import Namespace


def simulate_function(input_file):
    """ Call simulate with a complete Namespace input_file: Path to scenario json as String. """
    params = Namespace(
        input=input_file, strategy="greedy", margin=0.05, strategy_option=[], visual=False)
    simulate(params)


if __name__ == '__main__':
    simulate_function("examples/scenario.json")

    # simple example using the function to simulate multiple scenarios:
    # scenario_files = ["scenario1.json", "scenario2.json"]
    # for s in scenario_files:
    #     simulate_function(s)
