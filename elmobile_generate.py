"""
This script allows import of multiple SimBEV regions into SpiceEV scenarios.
Author: Moritz
"""

from pathlib import Path
from generate_from_simbev import generate_from_simbev
from argparse import Namespace
import csv
from itertools import islice


def generate_function(output, simbev, interval=15, price_seed=0, min_soc=0.5, min_soc_threshold=0.05,
                      include_ext_load_csv="gesamtlast_1Wo_Jan.csv", verbose=0, include_ext_csv_option=[],
                      include_feed_in_csv="feedin_input_szenario1_1Wo_Jan.csv",  include_feed_in_csv_option=[], include_price_csv="price_nsm_01_2wochen.csv", include_price_csv_option=[], v2g=0):
    params = Namespace(output=output, simbev=simbev, interval=interval, price_seed=price_seed, min_soc=min_soc,
                       include_ext_load_csv=include_ext_load_csv, include_ext_csv_option=include_ext_csv_option,
                       include_feed_in_csv=include_feed_in_csv, include_feed_in_csv_option=include_feed_in_csv_option,
                       include_price_csv=include_price_csv, include_price_csv_option=include_price_csv_option, min_soc_threshold=min_soc_threshold,
                       verbose=verbose, config=None, eps=1e-10, use_simbev_soc=True, gc_power=1000000, v2g=v2g,
                       vehicle_types="vehicle_types_elmobile.json")
    generate_from_simbev(params)


# input all data and call function here
if __name__ == '__main__':
    # set output directory; directory has to exist
    output_dir = Path("elmobile_data", "elmobile_scenario_1_Jan")
    output_dir.mkdir(exist_ok=True)
    # set the simbev directory; ususally only the last folder name needs adjustment
    simbev_dir = Path("..", "simbev", "simbev", "res", "elmobile_scenario_1_nov2g_2021-11-12_152458_simbev_run_1Wo_Jan")
    # set data csv file name, or None if not required
    ext_load_csv = "gesamtlast_1Wo_Jan.csv"
    feed_in_csv = "feedin_input_szenario1_1Wo_Jan.csv"
    include_price_csv = "price_nsm_01_2wochen.csv"

    # set csv file which contains 2 columns, region and amount of v2g vehicles
    file = Path(output_dir, "v2g_per_region_szenario1.csv")
    with open(file, 'r') as f:
        reader = csv.reader(f, delimiter=';')
        v2g_dict = {}
        for row in islice(reader, 1, None):
            v2g_dict[row[0]] = int(row[1])

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
        price_csv_option = [["column", "price"], ["step_duration_s", 900]]
        generate_function(Path(output_dir, plz + ".json"), dirs, include_ext_load_csv=ext_load_csv,
                          include_ext_csv_option=ext_csv_option, include_feed_in_csv=feed_in_csv,
                          include_feed_in_csv_option=feedin_csv_option, include_price_csv=include_price_csv, include_price_csv_option=price_csv_option, v2g=v2g)
        print("Region {} done! ({}/{})".format(plz, counter+1, len(plz_list)))
