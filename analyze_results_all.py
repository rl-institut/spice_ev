import pandas as pd
from pathlib import Path


# read all csv in the specified directory and calculate sums
def analyze_results_all(dirs):
    print("Calculating energy sums and characteristics of all SpiceEV results in " + str(dirs))
    output = None
    for file in dirs.rglob("*.csv"):
        file_df = pd.read_csv(file)
        cols = ["grid power [kW]", "feed-in [kW]", "ext.load [kW]", "sum CS power"]
        # check if format is correct
        if "grid power [kW]" in file_df and "feed-in [kW]" in file_df and "ext.load [kW]" in file_df and "sum CS power" in file_df:
            # calculation of sums and max values for each column
            if output is None:
                output = file_df.loc[:, cols].copy()
            else:
                output = output.add(file_df[cols])
        else:
            print(str(file) + " does not contain the necessary columns")

    output_path = Path(dirs, '0_analyzed_results_scenario_3_nov2g_Juli_balanced.csv')
    output.to_csv(output_path, sep=';')
    print("Resulting csv is saved as " + str(output_path))


if __name__ == '__main__':
    # set directory with SpiceEV results
    dir_path = Path("elmobile_data", 'elmobile_scenario_3_nov2g_Juli', 'res_balanced')
    analyze_results_all(dir_path)
