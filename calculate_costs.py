#!/usr/bin/env python3
import argparse
import csv
import datetime
import json

from spice_ev import util, costs


def read_simulation_csv(csv_file):
    """ Read prices, power values and charging signals for each timestamp from simulation results.

    :param csv_file: csv file with simulation results
    :type csv_file: str
    :return: timestamps, prices, power supplied from the grid, power fed into the grid, needed power
        of fixed load, charging signals
    :rtype: dict of lists
    """

    timestamps_list = []
    price_list = []  # [€/kWh]
    power_grid_supply_list = []  # [kW]
    power_fix_load_list = []  # [kW]
    power_generation_feed_in_list = []  # [kW]
    power_v2g_feed_in_list = []  # [kW]
    power_battery_feed_in_list = []  # [kW]
    charging_signal_list = []  # [-]
    power_schedule_list = []  # [kW]
    with open(csv_file, "r", newline="") as simulation_data:
        reader = csv.DictReader(simulation_data, delimiter=",")
        for row in reader:

            # find values for parameter
            timestamp = datetime.datetime.fromisoformat(row["time"])
            price = float(row.get("price [EUR/kWh]", 0))
            power_grid_supply = float(row.get("grid supply [kW]", 0))
            power_fix_load = max(float(row.get("fixed load [kW]", 0)) +
                                 min(float(row.get("local generation [kW]", 0)), 0) +
                                 min(float(row.get("battery power [kW]", 0)), 0) +
                                 min(float(row.get("sum CS power [kW]", 0)), 0), 0)
            power_generation_feed_in = float(row.get("generation feed-in [kW]", 0))
            power_v2g_feed_in = float(row.get("V2G feed-in [kW]", 0))
            power_battery_feed_in = float(row.get("battery feed-in [kW]", 0))

            # append value to the respective list:
            timestamps_list.append(timestamp)
            price_list.append(price)
            power_grid_supply_list.append(power_grid_supply)
            power_fix_load_list.append(power_fix_load)
            power_generation_feed_in_list.append(power_generation_feed_in)
            power_v2g_feed_in_list.append(power_v2g_feed_in)
            power_battery_feed_in_list.append(power_battery_feed_in)

            try:
                charging_signal = bool(int(row["window signal [-]"]))
            except KeyError:
                charging_signal = None

            try:
                power_schedule = float(row["schedule [kW]"])
                power_schedule_list.append(power_schedule)
            except KeyError:
                power_schedule_list = None

            charging_signal_list.append(charging_signal)

    return {
        "timestamps_list": timestamps_list,
        "price_list": price_list,
        "power_grid_supply_list": power_grid_supply_list,
        "power_fix_load_list": power_fix_load_list,
        "power_generation_feed_in_list": power_generation_feed_in_list,
        "power_v2g_feed_in_list": power_v2g_feed_in_list,
        "power_battery_feed_in_list": power_battery_feed_in_list,
        "charging_signal_list": charging_signal_list,
        "power_schedule_list": power_schedule_list,
    }


if __name__ == "__main__":  # pragma: no cover
    parser = argparse.ArgumentParser(
        description='Generate scenarios as JSON files for vehicle charging modelling')
    parser.add_argument('--grid-operator', '-go', default="default_grid_operator",
                        help='set grid operator for grid connector')
    parser.add_argument('--voltage-level', '-vl', help='Choose voltage level for cost calculation')
    parser.add_argument('--pv-power', type=int, default=0,
                        help='set nominal power for local photovoltaic power plant in kWp')
    parser.add_argument('--get-timeseries', '-ts', help='get timeseries from csv file.')
    parser.add_argument('--get-results', '-r', help='get simulation results from json file.')
    parser.add_argument('--cost-parameters-file', '-cp', help='get cost parameters from json file.')
    parser.add_argument('--config', help='Use config file to set arguments')

    args = parser.parse_args()

    util.set_options_from_config(args, check=parser, verbose=False)

    # load simulation results:
    with open(args.get_results, "r", newline="") as sj:
        simulation_json = json.load(sj)

    # strategy:
    strategy = simulation_json.get("charging_strategy", {}).get("strategy")
    assert strategy is not None, "Charging strategy not set in results file"

    # simulation interval in minutes:
    interval_min = simulation_json.get("temporal_parameters", {}).get("interval")
    assert interval_min is not None, "Simulation interval length not set in results file"

    # core standing time for fleet:
    core_standing_time_dict = simulation_json.get("core_standing_time")

    # load simulation time series:
    timeseries_lists = read_simulation_csv(args.get_timeseries)

    # grid connector
    gc = simulation_json.get("grid_connector", {})
    # grid operator of grid connector
    grid_operator = args.grid_operator or gc.get("grid_operator")
    # voltage level of grid connector:
    voltage_level = args.voltage_level or gc.get("voltage_level")
    assert voltage_level is not None, f"Voltage level is of {gc['gcID']} not defined"

    # cost calculation:
    costs.calculate_costs(
        grid_operator=grid_operator,
        strategy=strategy,
        voltage_level=voltage_level,
        interval=datetime.timedelta(minutes=interval_min),
        price_sheet_path=args.cost_parameters_file,
        results_json=args.get_results,
        power_pv_nominal=args.pv_power,
        **timeseries_lists
    )
