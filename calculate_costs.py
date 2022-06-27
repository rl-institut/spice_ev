#!/usr/bin/env python3
import argparse
import csv
import json
import datetime

from src.util import dt_within_core_standing_time

# data paths:
SIMULATION_DATA_PATH = "examples/simulation.csv"
SIMULATION_CFG_PATH = "examples/simulate.cfg"
SIMULATION_JSON_PATH = "examples/simulation.json"
PRICE_SHEET_PATH = "src/price_sheet.json"

# constants needed for the price sheet:
energy_supply_per_year_ec = 100000  # energy supply per year (needed for edge condition in order to define fee type)
utilization_time_per_year_ec = 2500  # utilization time of the grid (needed for edge condition in order to define fee type)


def read_simulation_csv(csv_file, strategy):
    """Reads prices, power values and charging signals for each timestamp from csv file that contains simulation results
    :param csv_file: csv file with simulation results
    :type csv_file: str
    :param strategy: charging strategy for electric vehicles
    :type strategy: str
    :return: timestamps, prices, power supplied from the grid, power fed into the grid, needed power of fix load, charging signals
    :rtype: list
    """

    timestamps_list = []
    price_list = []  # [€/kWh]
    power_grid_supply_list = []  # [kW]
    power_feed_in_list = []  # [kW]
    power_fix_load_list = []  # [kW]
    charging_signal_list = []  # [-]
    with open(csv_file) as simulation_data:
        reader = csv.DictReader(simulation_data, delimiter=",")
        for row in reader:

            # find value for parameter:
            timestamp = datetime.datetime.fromisoformat(row["time"])
            price = float(row["price [EUR/kWh]"])
            power_grid_supply = float(row["grid power [kW]"])
            power_feed_in = float(row["feed-in [kW]"])
            power_fix_load = float(row["ext.load [kW]"])

            # append value to the respective list:
            timestamps_list.append(timestamp)
            price_list.append(price)
            power_grid_supply_list.append(power_grid_supply)
            power_feed_in_list.append(power_feed_in)
            power_fix_load_list.append(power_fix_load)

            # for strategy flex_window find value of charging signal and append to list:
            if strategy == "flex_window":
                charging_signal = float(row["window GC1"])
                charging_signal_list.append(charging_signal)

    return (
        timestamps_list,
        price_list,
        power_grid_supply_list,
        power_feed_in_list,
        power_fix_load_list,
        charging_signal_list,
    )


def get_strategy_and_voltage_level(cfg_path):
    """Reads strategy and voltage level from simulation.cfg
    :param cfg_path: path of cfg-file with simulation data
    :type cfg_path: str
    :return: strategy and voltage level
    :rtype: str
    """

    with open("examples/simulate.cfg", "r") as f:
        for line in f:
            line = line.strip()
            if line.startswith("#"):
                # comment
                continue
            if len(line) == 0:
                # empty line
                continue
            k, v = line.split("=")
            k = k.strip()
            v = v.strip()

            if k == "strategy":
                strategy = v
            if k == "voltage_level":
                voltage_level = v
    return strategy, voltage_level


def get_flexible_load(power_grid_supply_list, power_fix_load_list):
    """Determines power of flexible load
    :param power_grid_supply_list: power supplied from the power grid
    :type power_grid_supply_list: list
    :param power_fix_load_list: power of the fix load
    :type power_fix_load_list: list
    :return: power of flexible load in kW
    :rtype: list
    """

    power_flex_load_list = []
    for number in range(len(power_grid_supply_list)):
        power_flex_load = power_grid_supply_list[number] - power_fix_load_list[number]
        power_flex_load_list.append(power_flex_load)
    return power_flex_load_list


def find_prices(
    strategy,
    voltage_level,
    utilization_time_per_year,
    energy_supply_per_year,
    utilization_time_per_year_ec,
):
    """Reads commodity and capacity charge from price sheets. For type 'slp' the capacity charge is equivalent to the
    basic charge.
    :param strategy: charging strategy for the electric vehicles
    :type strategy: str
    :param voltage_level: voltage level of the power grid
    :type voltage_level: str
    :param utilization_time_per_year: utilization time of the power grid per year
    :type utilization_time_per_year: int
    :param energy_supply_per_year: total energy supply from the power grid per year
    :type energy_supply_per_year: float
    :param utilization_time_per_year_ec: minimum value of the utilization time per year in order to use the right column of the price sheet (ec: edge condition)
    :type utilization_time_per_year_ec: int
    :return: commodity charge, capacity charge, fee type
    :rtype: float, float, str
    """

    with open("src/price_sheet.json") as ps:
        price_sheet = json.load(ps)

    if (
        strategy == "greedy"
        or strategy == "balanced"
        and abs(energy_supply_per_year) <= 100000
    ):
        fee_type = "slp"  # customer type 'slp'
        commodity_charge = price_sheet["grid fee"]["slp"]["commodity charge ct/kWh"][
            "net price"
        ]
        capacity_charge = price_sheet["grid fee"]["slp"]["basic charge EUR/a"][
            "net price"
        ]
    elif utilization_time_per_year < utilization_time_per_year_ec:
        fee_type = "jlp"  # customer type 'jlp'
        commodity_charge = price_sheet["grid fee"]["jlp"]["<2500 h/a"][
            "commodity charge ct/kWh"
        ][voltage_level]
        capacity_charge = price_sheet["grid fee"]["jlp"]["<2500 h/a"][
            "capacity charge EUR/kW*a"
        ][voltage_level]
    elif utilization_time_per_year >= utilization_time_per_year_ec:
        fee_type = "jlp"
        commodity_charge = price_sheet["grid fee"]["jlp"][">=2500 h/a"][
            "commodity charge ct/kWh"
        ][voltage_level]
        capacity_charge = price_sheet["grid fee"]["jlp"][">=2500 h/a"][
            "capacity charge EUR/kW*a"
        ][voltage_level]

    return commodity_charge, capacity_charge, fee_type


def calculate_commodity_costs(price_list, power_grid_supply_list, timestep_s):
    """Calculates commodity costs for all types of customers
    :param price_list: price list with commodity charge per timestamp
    :type price_list: list
    :param power_grid_supply_list: power supplied from the grid
    :type power_grid_supply_list: list
    :param timestep_s: simulation interval in seconds
    :type timestep_s: int
    :return: commodity costs per year and simulation period in eur
    :rtype: float
    """
    number_timestamps = len(power_grid_supply_list)
    duration_sim_s = (number_timestamps - 1) * timestep_s
    duration_year_s = 365 * 24 * 60 * 60

    # start value for commodity costs (variable gets updated with every timestep)
    commodity_costs_eur_sim = 0

    # create lists with energy supply per timestep:
    for number in range(len(power_grid_supply_list)):
        energy_supply_per_timestep = (
            power_grid_supply_list[number] * timestep_s / 3600
        )  # [kWh]
        commodity_costs_eur_sim = commodity_costs_eur_sim + (
            energy_supply_per_timestep * price_list[number] / 100
        )  # [€] negative for costs

    # calculate commodity costs:
    commodity_costs_eur_per_year = commodity_costs_eur_sim * (
        duration_year_s / duration_sim_s
    )

    return commodity_costs_eur_per_year, commodity_costs_eur_sim


def calculate_capacity_costs_jlp(
    capacity_charge, max_power_strategy, timestamps_list, timestep_s
):
    """Calculates the capacity costs per year and simulation period for jlp customers
    :param capacity_charge: capacity charge from price sheet
    :type capacity_charge: float
    :param max_power_strategy: power for the calculation of the capacity costs (individual per strategy)
    :type max_power_strategy: float
    :param timestamps_list: timestamps from simulation
    :type timestamps_list: list
    :return: capacity costs per year and simulation period (float)
    """

    number_timestamps = len(timestamps_list)
    duration_sim_s = (number_timestamps - 1) * timestep_s
    duration_year_s = 365 * 24 * 60 * 60

    capacity_costs_jlp_eur_per_year = capacity_charge * max_power_strategy  # [€]
    capacity_costs_jlp_eur_sim = capacity_costs_jlp_eur_per_year / (
        duration_year_s / duration_sim_s
    )  # [€]

    return capacity_costs_jlp_eur_per_year, capacity_costs_jlp_eur_sim


def calculate_costs(
    strategy,
    voltage_level,
    timestamps_list,
    power_grid_supply_list,
    price_list,
    power_fix_load_list,
    power_feed_in_list,
    charging_signal_list,
):
    """Calculate costs for the chosen charging strategy
    :param strategy: charging strategy
    :type strategy: str
    :param voltage_level: voltage level of the power grid the fleet is connected to
    :type voltage_level: str
    :param timestamps_list: timestamps from simulation
    :type timestamps_list: list
    :param power_grid_supply_list: power supplied from the grid
    :type power_grid_supply_list: list
    :param price_list: prices for energy supply in EUR/kWh
    :type price_list: list
    :param power_fix_load_list: power supplied from the grid for the fix load
    :type power_fix_load_list list
    :param power_feed_in_list: power fed into the grid
    :type power_feed_in_list: list
    :param charging_signal_list: charging signal given by the distribution system operator (1: charge, 0: don't charge)
    :type charging_signal_list: list
    :return: total costs per year and simulation period (fees and taxes included)
    """

    # SIMULATION DATA FROM JSON:
    with open(SIMULATION_JSON_PATH) as sj:
        simulation_json = json.load(sj)

    # PRICE SHEET
    with open(PRICE_SHEET_PATH) as ps:
        price_sheet = json.load(ps)

    # TEMPORAL PARAMETERS:
    interval_min = simulation_json["temporal parameters"]["interval"]
    timestep_s = interval_min * 60
    number_timestamps = len(timestamps_list)
    duration_sim_s = (number_timestamps - 1) * timestep_s
    duration_year_s = 365 * 24 * 60 * 60

    # ENERGY SUPPLY:
    energy_supply_sim = sum(power_grid_supply_list) * timestep_s / 3600
    energy_supply_per_year = energy_supply_sim * (duration_year_s / duration_sim_s)

    # COSTS FROM COMMODITY AND CAPACITY CHARGE DEPENDING ON CHARGING STRATEGY:
    if strategy == "greedy" or strategy == "balanced":
        """
        Calculates cost in accordance with the state of the art
        For slp customers the variable capacity_charge is equivalent to the bacic price
        """

        # maximum power supplied from the grid:
        max_power_grid_supply = min(
            power_grid_supply_list
        )  # minimum because of negative values [kW]

        # prices:
        utilization_time_per_year = abs(
            energy_supply_per_year / max_power_grid_supply
        )  # [h/a]
        commodity_charge, capacity_charge, fee_type = find_prices(
            strategy,
            voltage_level,
            utilization_time_per_year,
            energy_supply_per_year,
            utilization_time_per_year_ec,
        )

        # CAPACITY COSTS:
        if fee_type == "slp":
            capacity_costs_eur_per_year = -capacity_charge
            capacity_costs_eur_sim = capacity_costs_eur_per_year / (
                duration_year_s / duration_sim_s
            )
        else:  # jlp
            (
                capacity_costs_eur_per_year,
                capacity_costs_eur_sim,
            ) = calculate_capacity_costs_jlp(
                capacity_charge, max_power_grid_supply, timestamps_list, timestep_s
            )

        # COMMODITY COSTS:
        price_list = [
            commodity_charge,
        ] * len(power_grid_supply_list)
        (
            commodity_costs_eur_per_year,
            commodity_costs_eur_sim,
        ) = calculate_commodity_costs(price_list, power_grid_supply_list, timestep_s)

    elif strategy == "balanced_market":
        """New payment model the charging strategy 'balanced market' is based on
        For the charging strategy a price time series depending on the grid situation was created (initially depending
        on the left column of the price sheet). The fix and flexible load are charged separately.
        Commodity and capacity costs fix: The price is depending on the utilization time per year (as usual). For the
        utilization time the maximum fix load and the fix energy supply per year is used. Then the fix costs are
        calculated as usual.
        Commodity and capacity costs flexible: For the flexible load all prices are based on the right column of the
        price sheet (prices for grid friendly power supply). For the commodity charge the generated price time series is
        adjusted to the right column of the price sheet. Then the flexible commodity costs are calculated as usual. The
        flexible capacity costs are calculated only for grid supply in the high tariff window.
        """

        # COSTS FOR FIX LOAD

        # maximum fix power supplied from the grid:
        max_power_grid_supply_fix = min(
            power_fix_load_list
        )  # minimum because of negative values [kW]

        if max_power_grid_supply_fix == 0:  # no fix load existing
            commodity_costs_eur_per_year_fix, commodity_costs_eur_sim_fix = 0, 0
            capacity_costs_eur_per_year_fix, capacity_costs_eur_sim_fix = 0, 0
        else:  # fix load existing
            # fix energy supply:
            energy_supply_sim_fix = sum(power_fix_load_list) * timestep_s / 3600
            energy_supply_per_year_fix = energy_supply_sim_fix * (
                duration_year_s / duration_sim_s
            )

            # prices:
            utilization_time_per_year_fix = abs(
                energy_supply_per_year_fix / max_power_grid_supply_fix
            )  # [h/a]
            commodity_charge_fix, capacity_charge_fix, fee_type = find_prices(
                strategy,
                voltage_level,
                utilization_time_per_year_fix,
                energy_supply_per_year_fix,
                utilization_time_per_year_ec,
            )

            # commodity costs for fix load:
            price_list_fix_load = [
                commodity_charge_fix,
            ] * len(power_fix_load_list)
            (
                commodity_costs_eur_per_year_fix,
                commodity_costs_eur_sim_fix,
            ) = calculate_commodity_costs(
                price_list_fix_load, power_fix_load_list, timestep_s
            )

            # capacity costs for fix load:
            (
                capacity_costs_eur_per_year_fix,
                capacity_costs_eur_sim_fix,
            ) = calculate_capacity_costs_jlp(
                capacity_charge_fix,
                max_power_grid_supply_fix,
                timestamps_list,
                timestep_s,
            )

        # COSTS FOR FLEXIBLE LOAD

        power_flex_load_list = get_flexible_load(
            power_grid_supply_list, power_fix_load_list
        )

        # commodity charge used for comparisson of tariffs (comp: compare):
        # The price time series for the flexible load in balanced market is based on the left column of the price sheet.
        # Consequentely the prices from this column are needed for the comparison of the tariffs.
        utilization_time_per_year_comp = (
            utilization_time_per_year_ec - 1
        )  # needed in order to get prices from left column of the price sheet
        commodity_charge_comp, capacity_charge_comp, fee_type_comp = find_prices(
            strategy,
            voltage_level,
            utilization_time_per_year_comp,
            energy_supply_per_year,
            utilization_time_per_year_ec,
        )

        # adjust given price list (EUR/kWh --> ct/kWh)
        for number in range(len(price_list)):
            price_list[number] = price_list[number] * 100  # [ct/kWh]

        # low and medium tariff
        commodity_charge_lt = commodity_charge_comp * 0.68  # low tariff # [€/kWh]
        commodity_charge_mt = commodity_charge_comp * 1  # medium tariff # [€/kWh]

        # find power at times of high tariff:
        power_flex_load_ht_list = []
        for number in range(len(power_flex_load_list)):
            if (
                price_list[number] > 0
                and price_list[number] != commodity_charge_lt
                or price_list[number] != commodity_charge_mt
            ):
                power_flex_load_ht_list.append(power_flex_load_list[number])

        # maximum power for determination of capacity costs:
        max_power_costs = min(power_flex_load_ht_list)

        # capacity costs for flexible load:
        utilization_time_per_year = utilization_time_per_year_ec  # needed in order to use prices for grid friendly charging
        commodity_charge_flex, capacity_charge_flex, fee_type = find_prices(
            strategy,
            voltage_level,
            utilization_time_per_year,
            energy_supply_per_year,
            utilization_time_per_year_ec,
        )
        (
            capacity_costs_eur_per_year_flex,
            capacity_costs_eur_sim_flex,
        ) = calculate_capacity_costs_jlp(
            capacity_charge_flex, max_power_costs, timestamps_list, timestep_s
        )
        # price list for commodity charge for flexible load:
        ratio_commodity_charge = commodity_charge_flex / commodity_charge_comp
        for number in range(len(price_list)):
            price_list[number] = price_list[number] * ratio_commodity_charge  # [ct/kWh]

        # commodity costs for flexible load:
        (
            commodity_costs_eur_per_year_flex,
            commodity_costs_eur_sim_flex,
        ) = calculate_commodity_costs(price_list, power_grid_supply_list, timestep_s)

        # TOTAl COSTS:
        commodity_costs_eur_sim = (
            commodity_costs_eur_sim_fix + commodity_costs_eur_sim_flex
        )
        commodity_costs_eur_per_year = (
            commodity_costs_eur_per_year_fix + commodity_costs_eur_per_year_flex
        )
        capacity_costs_eur_sim = (
            capacity_costs_eur_sim_fix + capacity_costs_eur_sim_flex
        )
        capacity_costs_eur_per_year = (
            capacity_costs_eur_per_year_fix + capacity_costs_eur_per_year_flex
        )

    elif strategy == "flex_window":
        """New payment model based the charging strategy 'flex window'
        For the charging strategy a charging signal time series depending on the grid situation was created.
        The fix and flexible load are charged separately.
        Commodity and capacity costs fix: The price is depending on the utilization time per year (as usual). For the
        utilization time the maximum fix load and the fix energy supply per year is used. Then the fix costs are
        calculated as usual.
        Commodity and capacity costs flexible: For the flexible load all prices are based on the right column of the
        price sheet (prices for grid friendly power supply). Then the flexible commodity costs are calculated as usual.
        The flexible capacity costs are calculated only for grid supply in the high tariff window (signal = 0).
        """

        # COSTS FOR FIX LOAD

        # maximum fix power supplied from the grid:
        max_power_grid_supply_fix = min(
            power_fix_load_list
        )  # minimum because of negative values [kW]

        if max_power_grid_supply_fix == 0:  # no fix load existing
            commodity_costs_eur_per_year_fix, commodity_costs_eur_sim_fix = 0, 0
            capacity_costs_eur_per_year_fix, capacity_costs_eur_sim_fix = 0, 0
        else:  # fix load existing
            # fix energy supply:
            energy_supply_sim_fix = sum(power_fix_load_list) * timestep_s / 3600
            energy_supply_per_year_fix = energy_supply_sim_fix * (
                duration_year_s / duration_sim_s
            )

            # prices:
            utilization_time_per_year_fix = abs(
                energy_supply_per_year_fix / max_power_grid_supply_fix
            )  # [h/a]
            commodity_charge_fix, capacity_charge_fix, fee_type = find_prices(
                strategy,
                voltage_level,
                utilization_time_per_year_fix,
                energy_supply_per_year_fix,
                utilization_time_per_year_ec,
            )

            # commodity costs for fix load:
            price_list_fix_load = [
                commodity_charge_fix,
            ] * len(power_fix_load_list)
            (
                commodity_costs_eur_per_year_fix,
                commodity_costs_eur_sim_fix,
            ) = calculate_commodity_costs(
                price_list_fix_load, power_fix_load_list, timestep_s
            )

            # capacity costs for fix load:
            (
                capacity_costs_eur_per_year_fix,
                capacity_costs_eur_sim_fix,
            ) = calculate_capacity_costs_jlp(
                capacity_charge_fix,
                max_power_grid_supply_fix,
                timestamps_list,
                timestep_s,
            )

        # COSTS FOR FLEXIBLE LOAD

        power_flex_load_list = get_flexible_load(
            power_grid_supply_list, power_fix_load_list
        )

        # prices:
        utilization_time_per_year_flex = utilization_time_per_year_ec  # needed in order to use prices for grid friendly charging
        commodity_charge_flex, capacity_charge_flex, fee_type = find_prices(
            strategy,
            voltage_level,
            utilization_time_per_year_flex,
            energy_supply_per_year,
            utilization_time_per_year_ec,
        )

        # commodity costs for flexible load:
        price_list_flex_load = [
            commodity_charge_flex,
        ] * len(power_flex_load_list)
        (
            commodity_costs_eur_per_year_flex,
            commodity_costs_eur_sim_flex,
        ) = calculate_commodity_costs(
            price_list_flex_load, power_flex_load_list, timestep_s
        )

        # capacity costs for flexible load:
        power_flex_load_window_list = []
        for number in range(len(power_flex_load_list)):
            if charging_signal_list[number] == 0.0 and power_flex_load_list[number] < 0:
                power_flex_load_window_list.append(power_flex_load_list[number])
        if (
            power_flex_load_window_list == []
        ):  # no flexible capacity costs if charging takes place only when signal = 1
            capacity_costs_eur_per_year_flex, capacity_costs_eur_sim_flex = 0, 0
        else:
            max_power_grid_supply_flex = min(power_flex_load_window_list)
            (
                capacity_costs_eur_per_year_flex,
                capacity_costs_eur_sim_flex,
            ) = calculate_capacity_costs_jlp(
                capacity_charge_flex,
                max_power_grid_supply_flex,
                timestamps_list,
                timestep_s,
            )

        # TOTAl COSTS:
        commodity_costs_eur_sim = (
            commodity_costs_eur_sim_fix + commodity_costs_eur_sim_flex
        )
        commodity_costs_eur_per_year = (
            commodity_costs_eur_per_year_fix + commodity_costs_eur_per_year_flex
        )
        capacity_costs_eur_sim = (
            capacity_costs_eur_sim_fix + capacity_costs_eur_sim_flex
        )
        capacity_costs_eur_per_year = (
            capacity_costs_eur_per_year_fix + capacity_costs_eur_per_year_flex
        )

    elif strategy == "schedule":
        """New payment model for the charging strategy 'schedule'
        For the charging strategy a core standing time is chosen in which the distribution system operator can choose
        how the vehicles are charged. The fix and flexible load are charged separately.
        Commodity and capacity costs fix: The price is depending on the utilization time per year (as usual). For the
        utilization time the maximum fix load and the energy supply per year for the fix load is used.
        The commodity charge can be lowered by a flat fee in order to reimburse flexibility. Then the fix commodity and
        capacity costs are calculated as usual .
        Commodity and capacity costs flexible: For the flexible load all prices are based on the right column of the
        price sheet (prices for grid friendly power supply). Then the flexible commodity costs are calculated as usual.
        The capacity costs for the flexible load are calculated for the times outside of the core standing time only.
        """

        # core standing time for fleet:
        core_standing_time_dict = simulation_json["core standing time"]

        # COSTS FOR FIX LOAD

        # maximum fix power supplied from the grid:
        max_power_grid_supply_fix = min(
            power_fix_load_list
        )  # minimum wegen negativen Werten [kW]

        if max_power_grid_supply_fix == 0:  # no fix load existing
            commodity_costs_eur_per_year_fix, commodity_costs_eur_sim_fix = 0, 0
            capacity_costs_eur_per_year_fix, capacity_costs_eur_sim_fix = 0, 0
        else:  # fix load existing
            # fix energy supply:
            energy_supply_sim_fix = sum(power_fix_load_list) * timestep_s / 3600
            energy_supply_per_year_fix = energy_supply_sim_fix * (
                duration_year_s / duration_sim_s
            )

            # prices
            utilization_time_per_year_fix = abs(
                energy_supply_per_year_fix / max_power_grid_supply_fix
            )  # [h/a]
            commodity_charge_fix, capacity_charge_fix, fee_type = find_prices(
                strategy,
                voltage_level,
                utilization_time_per_year_fix,
                energy_supply_per_year_fix,
                utilization_time_per_year_ec,
            )
            reduction_commodity_charge = price_sheet[
                "strategy related cost parameters"
            ]["schedule"]["reduction of commodity charge"]
            commodity_charge_fix = commodity_charge_fix - reduction_commodity_charge

            # commodity costs for fix load:
            price_list_fix_load = [
                commodity_charge_fix,
            ] * len(power_fix_load_list)
            (
                commodity_costs_eur_per_year_fix,
                commodity_costs_eur_sim_fix,
            ) = calculate_commodity_costs(
                price_list_fix_load, power_fix_load_list, timestep_s
            )

            # capacity costs for fix load:
            max_power_grid_supply_fix = min(power_fix_load_list)
            (
                capacity_costs_eur_per_year_fix,
                capacity_costs_eur_sim_fix,
            ) = calculate_capacity_costs_jlp(
                capacity_charge_fix,
                max_power_grid_supply_fix,
                timestamps_list,
                timestep_s,
            )

        # COSTS FOR FLEXIBLE LOAD

        # power of flexible load:
        power_flex_load_list = get_flexible_load(
            power_grid_supply_list, power_fix_load_list
        )

        # prices:
        utilization_time_per_year_flex = utilization_time_per_year_ec  # needed in order to use prices for grid friendly charging
        commodity_charge_flex, capacity_charge_flex, fee_type = find_prices(
            strategy,
            voltage_level,
            utilization_time_per_year_flex,
            energy_supply_per_year,
            utilization_time_per_year_ec,
        )

        # commodity costs for flexible load:
        price_list_flex_load = [
            commodity_charge_flex,
        ] * len(power_flex_load_list)
        (
            commodity_costs_eur_per_year_flex,
            commodity_costs_eur_sim_flex,
        ) = calculate_commodity_costs(
            price_list_flex_load, power_flex_load_list, timestep_s
        )

        # capacity costs for flexible load:
        power_outside_core_standing_time_flex_list = []
        for number in range(
            len(timestamps_list)
        ):  # find times of grid supply outside of core standing time
            if (
                dt_within_core_standing_time(
                    timestamps_list[number], core_standing_time_dict
                )
                == False
                and power_flex_load_list[number] < 0
            ):  # not within core standing time
                power_outside_core_standing_time_flex_list.append(
                    power_flex_load_list[number]
                )
        max_power_grid_supply_outside_cst_flex = min(
            power_outside_core_standing_time_flex_list
        )  # cst: core standing time
        (
            capacity_costs_eur_per_year_flex,
            capacity_costs_eur_sim_flex,
        ) = calculate_capacity_costs_jlp(
            capacity_charge_flex,
            max_power_grid_supply_outside_cst_flex,
            timestamps_list,
            timestep_s,
        )

        # TOTAl COSTS:
        commodity_costs_eur_sim = (
            commodity_costs_eur_sim_fix + commodity_costs_eur_sim_flex
        )
        commodity_costs_eur_per_year = (
            commodity_costs_eur_per_year_fix + commodity_costs_eur_per_year_flex
        )
        capacity_costs_eur_sim = (
            capacity_costs_eur_sim_fix + capacity_costs_eur_sim_flex
        )
        capacity_costs_eur_per_year = (
            capacity_costs_eur_per_year_fix + capacity_costs_eur_per_year_flex
        )

    # COSTS NOT RELATED TO STRATEGIES

    # ADDITIONAL COSTS FOR JLP-CONSUMERS:
    if fee_type == "jlp":
        additional_costs_per_year = price_sheet["grid fee"]["jlp"]["additional costs"][
            "costs"
        ]
        additional_costs_sim = additional_costs_per_year * (
            duration_sim_s / duration_year_s
        )
    else:
        additional_costs_per_year, additional_costs_sim = 0, 0

    # COSTS FOR POWER PROCUREMENT:
    power_procurement_charge = price_sheet["power procurement"]["charge"]  # [ct/kWh]
    power_procurement_costs_sim = (
        power_procurement_charge * energy_supply_sim / 100
    )  # [EUR]
    power_procurement_costs_per_year = power_procurement_costs_sim * (
        duration_year_s / duration_sim_s
    )  # [EUR]

    # COSTS FROM LEVIES:

    # prices:
    eeg_levy = price_sheet["levies"]["EEG-levy"]  # [ct/kWh]
    chp_levy = price_sheet["levies"][
        "chp levy (art. 26 und 26a KWKG 2020)"
    ]  # [ct/kWh], chp: combined heat and power
    individual_charge_levy = price_sheet["levies"][
        "individual charge levy (art. 19 Abs. 2 StromNEV)"
    ]  # [ct/kWh]
    offshore_levy = price_sheet["levies"][
        "Offshore levy (art. 17f Absatz 7 EnWG)"
    ]  # [ct/kWh]
    interruptible_loads_levy = price_sheet["levies"][
        "interruptible loads levy (art. 18 AbLaV)"
    ]  # [ct/kWh]

    # costs for simulation_period:
    eeg_costs_sim = eeg_levy * energy_supply_sim / 100  # [EUR]
    chp_costs_sim = chp_levy * energy_supply_sim / 100  # [EUR]
    individual_charge_costs_sim = (
        individual_charge_levy * energy_supply_sim / 100
    )  # [EUR]
    offshore_costs_sim = offshore_levy * energy_supply_sim / 100  # [EUR]
    interruptible_loads_costs_sim = (
        interruptible_loads_levy * energy_supply_sim / 100
    )  # [EUR]
    levies_costs_total_sim = (
        eeg_costs_sim
        + eeg_costs_sim
        + individual_charge_costs_sim
        + offshore_costs_sim
        + interruptible_loads_costs_sim
    )

    # costs per year:
    eeg_costs_per_year = eeg_costs_sim * (duration_year_s / duration_sim_s)  # [EUR]
    chp_costs_per_year = chp_costs_sim * (duration_year_s / duration_sim_s)  # [EUR]
    individual_charge_costs_per_year = individual_charge_costs_sim * (
        duration_year_s / duration_sim_s
    )  # [EUR]
    offshore_costs_per_year = offshore_costs_sim * (
        duration_year_s / duration_sim_s
    )  # [EUR]
    interruptible_loads_costs_per_year = interruptible_loads_costs_sim * (
        duration_year_s / duration_sim_s
    )  # [EUR]

    # COSTS FROM CONCESSION FEE:

    concession_fee = price_sheet["concession fee"]["charge"]  # [ct/kWh]
    concession_fee_costs_sim = concession_fee * energy_supply_sim / 100  # [EUR]
    concession_fee_costs_per_year = concession_fee_costs_sim * (
        duration_year_s / duration_sim_s
    )  # [EUR]

    # COSTS FROM FEED-IN REMUNERATION:

    # get nominal power of pv power plant:
    power_pv_nominal = simulation_json["photovoltaics"][
        "nominal power"
    ]  # ['duration'][0]#['Nettopreis']

    # find charge for PV remuneration
    pv_nominal_power_max = price_sheet["feed-in remuneration"]["PV"]["kWp"]
    pv_remuneration = price_sheet["feed-in remuneration"]["PV"]["remuneration"]
    # v2g_remuneration = price_sheet['feed-in remuneration']['V2G'] # [ct/kWh] #placeholder
    if power_pv_nominal <= pv_nominal_power_max[0]:
        feed_in_charge_pv = pv_remuneration[0]  # [ct/kWh]
    elif power_pv_nominal <= pv_nominal_power_max[1]:
        feed_in_charge_pv = pv_remuneration[1]  # [ct/kWh]
    elif power_pv_nominal <= pv_nominal_power_max[2]:
        feed_in_charge_pv = pv_remuneration[2]  # [ct/kWh]
    else:
        raise ValueError(
            "Feed-in remuneration for PV is calculated only for nominal powers of up to 100 kWp"
        )

    # energy feed in by PV power plant:
    energy_feed_in_pv_sim = sum(power_feed_in_list) * timestep_s / 3600  # [kWh]
    energy_feed_in_pv_per_year = energy_feed_in_pv_sim * (
        duration_year_s / duration_sim_s
    )  # [kWh]

    # costs for PV feed-in:
    pv_feed_in_costs_sim = energy_feed_in_pv_sim * feed_in_charge_pv / 100  # [EUR]
    pv_feed_in_costs_per_year = (
        energy_feed_in_pv_per_year * feed_in_charge_pv / 100
    )  # [EUR]

    # COSTS FROM TAXES AND TOTAL COSTS:

    # electricity tax:
    electricity_tax = price_sheet["taxes"]["tax on electricity"]  # [ct/kWh]
    electricity_tax_costs_sim = electricity_tax * energy_supply_sim / 100  # [EUR]
    electricity_tax_costs_per_year = electricity_tax_costs_sim * (
        duration_year_s / duration_sim_s
    )  # [EUR]

    # value added tax:
    value_added_tax_percent = price_sheet["taxes"]["value added tax"]  # [%]
    value_added_tax = value_added_tax_percent / 100  # [-]

    # total costs without value added tax (commodity costs and capacity costs included):
    costs_total_not_value_added_eur_sim = (
        commodity_costs_eur_sim
        + capacity_costs_eur_sim
        + power_procurement_costs_sim
        + additional_costs_sim
        + levies_costs_total_sim
        + concession_fee_costs_sim
        + electricity_tax_costs_sim
    )  # [EUR]
    costs_total_not_value_added_eur_per_year = costs_total_not_value_added_eur_sim * (
        duration_year_s / duration_sim_s
    )  # [EUR]

    # costs from value added tax:
    value_added_tax_costs_sim = value_added_tax * costs_total_not_value_added_eur_sim
    value_added_tax_costs_per_year = (
        value_added_tax * costs_total_not_value_added_eur_per_year
    )

    # total costs with value added tax (commodity costs and capacity costs included):
    costs_total_value_added_eur_sim = (
        costs_total_not_value_added_eur_sim + value_added_tax_costs_sim
    )
    costs_total_value_added_eur_per_year = (
        costs_total_not_value_added_eur_per_year + value_added_tax_costs_per_year
    )

    # MORE ADAPTATIONS REGARDING THE CUSTOMER TYPE:

    capacity_or_basic_costs = "capacity costs"

    if strategy == "greedy" or strategy == "balanced":
        commodity_costs_eur_per_year_fix = (
            "no differentiation between fix and flexible load"
        )
        commodity_costs_eur_sim_fix = "no differentiation between fix and flexible load"
        capacity_costs_eur_per_year_fix = (
            "no differentiation between fix and flexible load"
        )
        capacity_costs_eur_sim_fix = "no differentiation between fix and flexible load"
        commodity_costs_eur_per_year_flex = (
            "no differentiation between fix and flexible load"
        )
        commodity_costs_eur_sim_flex = (
            "no differentiation between fix and flexible load"
        )
        capacity_costs_eur_per_year_flex = (
            "no differentiation between fix and flexible load"
        )
        capacity_costs_eur_sim_flex = "no differentiation between fix and flexible load"

        capacity_or_basic_costs = "basic costs"

    # WRITE ALL COSTS INTO JSON:

    json_results_costs = {}

    json_results_costs["Costs"] = {
        "electricity costs": {
            "per year": {
                "total (brutto)": costs_total_value_added_eur_per_year
                + pv_feed_in_costs_per_year,
                "grid fee": {
                    "total grid fee": commodity_costs_eur_per_year
                    + capacity_costs_eur_per_year,
                    "commodity costs": {
                        "total costs": commodity_costs_eur_per_year,
                        "costs for fix load": commodity_costs_eur_per_year_fix,
                        "costs for flexible load": commodity_costs_eur_per_year_flex,
                    },
                    capacity_or_basic_costs: {
                        "total costs": capacity_costs_eur_per_year,
                        "costs for fix load": capacity_costs_eur_per_year_fix,
                        "costs for flexible load": capacity_costs_eur_per_year_flex,
                    },
                    "additional costs": additional_costs_per_year,
                },
                "power procurement": power_procurement_costs_per_year,
                "levies": {
                    "EEG-levy": eeg_costs_per_year,
                    "chp levy (art. 26 und 26a KWKG 2020)": chp_costs_per_year,
                    "individual charge levy (art. 19 Abs. 2 StromNEV)": individual_charge_costs_per_year,
                    "Offshore levy (art. 17f Absatz 7 EnWG)": offshore_costs_per_year,
                    "interruptible loads levy (art. 18 AbLaV)": interruptible_loads_costs_per_year,
                },
                "concession fee": concession_fee_costs_per_year,
                "taxes": {
                    "value added tax": value_added_tax_costs_per_year,
                    "tax on electricity": electricity_tax_costs_per_year,
                },
                "feed-in remuneration": {"PV": pv_feed_in_costs_per_year, "V2G": 0},
                "unit": "EUR",
                "info": "energy costs for one year",
            },
            "for simulation period": {
                "total (brutto)": costs_total_value_added_eur_sim
                + pv_feed_in_costs_sim,  # ergänzen
                "grid fee": {
                    "total grid fee": commodity_costs_eur_sim + capacity_costs_eur_sim,
                    "commodity costs": {
                        "total costs": commodity_costs_eur_sim,
                        "costs for fix load": commodity_costs_eur_sim_fix,
                        "costs for flexible load": commodity_costs_eur_sim_flex,
                    },
                    capacity_or_basic_costs: {
                        "total costs": capacity_costs_eur_sim,
                        "costs for fix load": capacity_costs_eur_sim_fix,
                        "costs for flexible load": capacity_costs_eur_sim_flex,
                    },
                    "additional costs": additional_costs_sim,
                },
                "power procurement": power_procurement_costs_sim,
                "levies": {
                    "EEG-levy": eeg_costs_sim,
                    "chp levy (art. 26 und 26a KWKG 2020)": chp_costs_sim,
                    "individual charge levy (art. 19 Abs. 2 StromNEV)": individual_charge_costs_sim,
                    "Offshore levy (art. 17f Absatz 7 EnWG)": offshore_costs_sim,
                    "interruptible loads levy (art. 18 AbLaV)": interruptible_loads_costs_sim,
                },
                "concession fee": concession_fee_costs_sim,
                "taxes": {
                    "value added tax": value_added_tax_costs_sim,
                    "tax on electricity": electricity_tax_costs_sim,
                },
                "feed-in remuneration": {"PV": pv_feed_in_costs_sim, "V2G": 0},
                "unit": "EUR",
                "info": "energy costs for simulation period",
            },
        }
    }

    # add dictionary to json with simulation data:
    with open(SIMULATION_JSON_PATH, "r+") as sj:
        simulation_json = json.load(sj)
        simulation_json.update(json_results_costs)
        sj.seek(0)
        json.dump(simulation_json, sj, indent=4)

    return (
        costs_total_value_added_eur_per_year + pv_feed_in_costs_per_year,
        costs_total_value_added_eur_sim + pv_feed_in_costs_sim,
    )


if __name__ == "__main__":
    run_cost_calc = True

    strategy, voltage_level = get_strategy_and_voltage_level(SIMULATION_CFG_PATH)

    (
        timestamps_list,
        price_list,
        power_grid_supply_list,
        power_feed_in_list,
        power_fix_load_list,
        charging_signal_list,
    ) = read_simulation_csv(SIMULATION_DATA_PATH, strategy)

    calculate_costs(
        strategy,
        voltage_level,
        timestamps_list,
        power_grid_supply_list,
        price_list,
        power_fix_load_list,
        power_feed_in_list,
        charging_signal_list,
    )
