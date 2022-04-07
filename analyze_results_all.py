import pandas as pd
from pathlib import Path


# read all csv in the specified directory and calculate sums
def analyze_results_all(dirs):
    print("Calculating energy sums and characteristics of all SpiceEV results in " + str(dirs))
    output = pd.DataFrame()
    for file in dirs.rglob("*.csv"):
        file_df = pd.read_csv(file)
        # check if format is correct?
        # timestep in hours
        timestep = 1 / 4
        if "grid power [kW]" in file_df and "feed-in [kW]" in file_df and "ext.load [kW]" in file_df and "sum CS power" in file_df:
            # calculation of sums and max values for each column
            grid_power_plz = file_df.loc[:, "grid power [kW]"]
            feed_in_plz = file_df.loc[:, "feed-in [kW]"]
            building_plz = file_df.loc[:, "ext.load [kW]"]
            charging_plz = file_df.loc[:, "sum CS power"]
        else:
            print(str(file) + " does not contain the necessary columns")
            continue

        for #addiere jede neue PLZ-Zeitreihe zur Summe hinzu
            #output: csv-Datei mit 4 Zeitreihen (grid, feed-in, building, charging) über ganz Berlin (also Summe aller PLZ)

            grid_power_all = grid_power_all + grid_power_plz
            feed_in_all = feed_in_all + feed_in_plz
            building_all = building_all + building_plz
            charging_all = charging_all + charging_plz

            result = {
                "grid_power_kw": grid_power_all,
                "building_in_kw": building_all,
                "charging_kw": charging_all,
                "feed_in_kw": feed_in_all
            }
            output = output.append(result, ignore_index=True)

    output_path = Path(dirs, '0_analyzed_results_scenario_3_nov2g_Juli_balanced.csv')
    output.to_csv(output_path, sep=';')
    print("Resulting csv is saved as " + str(output_path))


if __name__ == '__main__':
    # set directory with SpiceEV results
    dir_path = Path("elmobile_data", 'elmobile_scenario_3_nov2g_Juli', 'res_balanced')
    analyze_results_all(dir_path)
