from simulate import simulate
from argparse import Namespace

# This script is an example on how to build a Namespace object that's required by the simulate function
# To run, one of the following conditions must be met:
# - this script is located in the main spice_ev Folder
# - spice_ev is added to the pythonpath


def simulate_function(input_file):
    """Call simulate with a complete Namespace
    input_file: Path to scenario json as String
    """
    params = Namespace(input=input_file, strategy="greedy", margin=0.05, strategy_option=[], visual=False,
                       output=None, config=None)
    simulate(params)


if __name__ == '__main__':
    simulate_function("scenario.json")

    # simple example using the function to simulate multiple scenarios:
    # scenario_files = ["scenario1.json", "scenario2.json"]
    # for s in scenario_files:
    #     simulate_function(s)
