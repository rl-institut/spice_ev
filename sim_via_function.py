from .simulate import simulate
from argparse import Namespace

# This script is an example on how to build a Namespace object that's required by the simulate function


def simulate_function(input_file):
    """Call simulate with a complete Namespace
    input_file: Path to scenario json as String
    """
    params = Namespace(input=input_file, strategy="greedy", margin=0.05, strategy_option=[], visual=False,
                       output=None, config=None)
    simulate(params)


if __name__ == '__main__':
    simulate_function("examples/scenario.json")

    # simple example using the function to simulate multiple scenarios:
    # scenario_files = ["scenario1.json", "scenario2.json"]
    # for s in scenario_files:
    #     simulate_function(s)
