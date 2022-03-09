import pandas as pd
from pathlib import Path


# read all csv in the specified directory and calculate sums
def analyze_results(dirs):
    print("Calculating energy sums and characteristics of all SpiceEV results in " + str(dirs))
    output = pd.DataFrame()
    for file in dirs.rglob("*.csv"):
        file_df = pd.read_csv(file)
        # check if format is correct?
        # calculate sums for every region
        # timestep in hours
        timestep = 1 / 4
        if "grid power [kW]" in file_df and "feed-in [kW]" in file_df and "ext.load [kW]" in file_df and "sum CS power" in file_df:
            # calculation of sums and max values for each column
            grid_power_max = round(file_df.loc[:, "grid power [kW]"].max(), 2)
            grid_power_sum = round(file_df.loc[:, "grid power [kW]"].sum() * timestep, 2)
            feed_in_max = round(file_df.loc[:, "feed-in [kW]"].max(), 2)
            feed_in_sum = round(file_df.loc[:, "feed-in [kW]"].sum() * timestep, 2)
            building_in_max = round(file_df.loc[:, "ext.load [kW]"].max(), 2)
            building_in_sum = round(file_df.loc[:, "ext.load [kW]"].sum() * timestep, 2)
            max_charging = round(file_df.loc[:, "sum CS power"].max(), 2)
            total_charging = round(file_df.loc[:, "sum CS power"].sum() * timestep, 2)
            # calculation of surplus
            diff_sum = feed_in_sum + building_in_sum + total_charging
            if diff_sum > 0:
                surplus = 0
            else:
                surplus = diff_sum
            # calculation of characteristics
            feed_in_used = feed_in_sum - surplus
            e = round(feed_in_used / feed_in_sum * 100, 2)  # TODO
            a = round(feed_in_used / (grid_power_sum+feed_in_used) * 100, 2)  # how is autarkiegrad defined in this system
            result = {
                "plz": file.stem,
                "grid_power_max_kw": grid_power_max,
                "grid_power_sum_kwh": grid_power_sum,
                "building_in_max_kw": building_in_max,
                "building_in_sum-kwh": building_in_sum,
                "charging_max_kw": max_charging,
                "charging_sum_kwh": total_charging,
                "feed_in_max_kw": feed_in_max,
                "feed_in_sum_kwh": feed_in_sum,
                "surplus": surplus,
                "feed_in_used": feed_in_used,
                "pv_consumption_%": e,
                "self_sufficiency_%": a
            }
            output = output.append(result, ignore_index=True)
        else:
            print(str(file) + " does not contain the necessary columns")
            continue
    for json_file in dirs.rglob("*.json"):
        json_df = pd.read_json(json_file)
        plz_row = output.loc[output["plz"] == json_file.stem]
        # select values here
        sum_energy_vehicles = json_df.at["value", "sum_energy_vehicles"]
        output.loc[plz_row.index, "sum_energy_vehicles"] = sum_energy_vehicles
    output_path = Path(dirs, '0_analyzed_results_scenario_1_v2g_Jan_balanced.csv')
    output.to_csv(output_path, sep=';')
    print("Resulting csv is saved as " + str(output_path))


if __name__ == '__main__':
    # set directory with SpiceEV results
    dir_path = Path("elmobile_data", 'elmobile_scenario_1_Jan', 'res_balanced')
    analyze_results(dir_path)
