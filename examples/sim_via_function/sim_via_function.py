from generate_from_simbev import generate_from_simbev
from pathlib import Path
import pandas as pd
from argparse import Namespace

# This script is an example that as is only works with local simBEV files (change Path and simbev_folder)

def generate_function(output, simbev, interval=15, price_seed=0, min_soc=0.5, min_soc_threshold=0.05,
                      include_ext_load_csv=False, include_ext_csv_option=False, include_feed_in_csv=False,
                      include_feed_in_csv_option=False, include_price_csv=False, include_price_csv_option=False):
    """Call generate_from_simbev with a complete Namespace
    output: filename.json
    simbev: Path to simBEV result directory
    """
    params = Namespace(output=output, simbev=simbev, interval=interval, price_seed=price_seed, min_soc=min_soc,
                       min_soc_threshold=min_soc_threshold, include_ext_load_csv=include_ext_load_csv,
                       include_ext_csv_option=include_ext_csv_option, include_feed_in_csv=include_feed_in_csv,
                       include_feed_in_csv_option=include_feed_in_csv_option, include_price_csv=include_price_csv,
                       include_price_csv_option=include_price_csv_option, config=False)
    generate_from_simbev(params)


if __name__ == '__main__':
    scenario = pd.read_csv("input_args.csv", sep=';')
    simbev_folder = "default_multi_2021-05-31_100746_simbev_run"

    for plz in scenario['PLZ']:
        plz_string = str(plz)
        simbev = Path("../../..", "simbev", "simbev", "res", simbev_folder, plz_string)
        output = Path("res", plz_string + ".json")
        generate_function(output, simbev)
