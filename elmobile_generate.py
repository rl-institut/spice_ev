"""
This script allows import of multiple SimBEV regions into SpiceEV scenarios.
Author: Moritz
"""

from pathlib import Path
from generate_from_simbev import generate_from_simbev
from argparse import Namespace
import csv


def generate_function(output, simbev, interval=15, price_seed=0, min_soc=0.5, min_soc_threshold=0.05,
                      include_ext_load_csv="gesamtlast.csv", verbose=0, include_ext_csv_option=[],
                      include_feed_in_csv="feedin_input_status_quo.csv", include_feed_in_csv_option=[], v2g=0):
    params = Namespace(output=output, simbev=simbev, interval=interval, price_seed=price_seed, min_soc=min_soc,
                       include_ext_load_csv=include_ext_load_csv, include_ext_csv_option=include_ext_csv_option,
                       include_feed_in_csv=include_feed_in_csv, include_feed_in_csv_option=include_feed_in_csv_option,
                       include_price_csv=None, include_price_csv_option=[], min_soc_threshold=min_soc_threshold,
                       verbose=verbose, config=None, eps=1e-10, use_simbev_soc=True, gc_power=1000000, v2g=v2g)
    generate_from_simbev(params)


# input all data and call function here
if __name__ == '__main__':
    # set output directory; directory has to exist
    output_dir = Path("elmobile_data", "scenario_1")
    output_dir.mkdir(exist_ok=True)
    # set the simbev directory; ususally only the last folder name needs adjustment
    simbev_dir = Path("..", "simbev", "simbev", "res", "elmobile_status_quo_2021-08-11_133042_simbev_run")
    # set data csv file name, or None if not required
    ext_load_csv = "gesamtlast.csv"
    feed_in_csv = "feedin_input_status_quo.csv"

    # look for csv file with v2g in name in output directory and parse into dict
    for file in output_dir.rglob("*.csv"):
        if 'v2g' in file:
            with open(file, 'r') as f:
                reader = csv.reader(f)
                v2g_dict = {}
                for row in reader:
                    v2g_dict[row[0]] = row[1]
            break

    plz_list = [f.name for f in simbev_dir.iterdir() if f.is_dir()]
    for counter, plz in enumerate(plz_list):
        dirs = Path(simbev_dir, plz)
        # time steps of the input csv are declared here (900s = 15mins, 3600s = 1h)
        if 'v2g_dict' in locals():
            v2g = v2g_dict[plz]
        else:
            v2g = 0
        ext_csv_option = [["column", plz], ["step_duration_s", 900]]
        feedin_csv_option = [["column", plz], ["step_duration_s", 3600]]
        generate_function(Path(output_dir, plz + ".json"), dirs, include_ext_load_csv=ext_load_csv,
                          include_ext_csv_option=ext_csv_option, include_feed_in_csv=feed_in_csv,
                          include_feed_in_csv_option=feedin_csv_option, v2g=v2g)
        print("Region {} done! ({}/{})".format(plz, counter+1, len(plz_list)))
