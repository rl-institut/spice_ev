#!/usr/bin/env python3
import csv
import json
import datetime
import argparse
from src.util import set_options_from_config

from src.util import dt_within_core_standing_time

# constants:
# constant utilization time of the grid needed for the price sheet in order to define fee type
UTILIZATION_TIME_PER_YEAR_EC = 2500  # ec: edge condition, [h/a]
# duration of one year
DURATION_YEAR_S = 365 * 24 * 60 * 60  # [s]


def read_simulation_csv(csv_file, strategy):
    """Reads prices, power values and charging signals for each timestamp from csv file that
    contains simulation results
    :param csv_file: csv file with simulation results
    :type csv_file: str
    :param strategy: charging strategy for electric vehicles
    :type strategy: str
    :return: timestamps, prices, power supplied from the grid, power fed into the grid, needed power
    of fix load, charging signals
    :rtype: list
    """

    timestamps_list = []
    price_list = []  # [€/kWh]
    power_grid_supply_list = []  # [kW]
    power_feed_in_list = []  # [kW]
    power_fix_load_list = []  # [kW]
    charging_signal_list = []  # [-]
    with open(csv_file, "r", newline="") as simulation_data:
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


def get_duration_of_simulation_period(simulation_data_list, timestep_s):
    """Determines duration of simulated period
    :param simulation_data_list: any complete list with simulation data (e.g. timestamps_list)
    :type simulation_data_list: list
    :param timestep_s: duration of simulation interval in seconds
    :type timestep_s: int
    :return: duration of simulation period in seconds
    :rtype: int
    """
    number_timestamps = len(simulation_data_list)
    duration_sim_s = (number_timestamps - 1) * timestep_s

    return duration_sim_s


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
    for i in range(len(power_grid_supply_list)):
        power_flex_load = power_grid_supply_list[i] - power_fix_load_list[i]
        power_flex_load_list.append(power_flex_load)

    return power_flex_load_list


def find_prices(price_sheet_path, strategy, voltage_level, utilization_time_per_year,
                energy_supply_per_year, utilization_time_per_year_ec=UTILIZATION_TIME_PER_YEAR_EC):
    """Reads commodity and capacity charge from price sheets. For type 'SLP' the capacity charge is
    equivalent to the basic charge.
    :param strategy: charging strategy for the electric vehicles
    :type strategy: str
    :param voltage_level: voltage level of the power grid
    :type voltage_level: str
    :param utilization_time_per_year: utilization time of the power grid per year
    :type utilization_time_per_year: int
    :param energy_supply_per_year: total energy supply from the power grid per year
    :type energy_supply_per_year: float
    :param utilization_time_per_year_ec: minimum value of the utilization time per year in order to
    use the right column of the price sheet (ec: edge condition)
    :type utilization_time_per_year_ec: int
    :return: commodity charge, capacity charge, fee type
    :rtype: float, float, str
    """

    with open(price_sheet_path, "r", newline="") as ps:
        price_sheet = json.load(ps)
    if (strategy == "greedy" or strategy == "balanced") and abs(energy_supply_per_year) <= 100000:
        # customer type 'SLP'
        fee_type = "SLP"
        commodity_charge = price_sheet["grid_fee"]["SLP"]["commodity_charge_ct/kWh"]["net_price"]
        capacity_charge = price_sheet["grid_fee"]["SLP"]["basic_charge_EUR/a"]["net_price"]
    elif utilization_time_per_year < utilization_time_per_year_ec:
        # customer type 'RLM' with utilization_time_per_year < utilization_time_per_year_ec
        fee_type = "RLM"
        commodity_charge = price_sheet["grid_fee"]["RLM"][
            "<" + str(utilization_time_per_year_ec) + "_h/a"][
            "commodity_charge_ct/kWh"][voltage_level]
        capacity_charge = price_sheet["grid_fee"]["RLM"][
            "<" + str(utilization_time_per_year_ec) + "_h/a"][
            "capacity_charge_EUR/kW*a"][voltage_level]
    else:
        # customer type 'RLM' with utilization_time_per_year >= utilization_time_per_year_ec
        fee_type = "RLM"
        commodity_charge = price_sheet["grid_fee"]["RLM"][
            ">=" + str(utilization_time_per_year_ec) + "_h/a"][
            "commodity_charge_ct/kWh"][voltage_level]
        capacity_charge = price_sheet["grid_fee"]["RLM"][
            ">=" + str(utilization_time_per_year_ec) + "_h/a"][
            "capacity_charge_EUR/kW*a"][voltage_level]

    return commodity_charge, capacity_charge, fee_type


def calculate_commodity_costs(price_list, power_grid_supply_list, timestep_s,
                              duration_year_s=DURATION_YEAR_S):
    """Calculates commodity costs for all types of customers
    :param price_list: price list with commodity charge per timestamp
    :type price_list: list
    :param power_grid_supply_list: power supplied from the grid
    :type power_grid_supply_list: list
    :param timestep_s: simulation interval in seconds
    :type timestep_s: int
    :param duration_year_s: duration of one year in seconds
    :type duration_year_s: int
    :return: commodity costs per year and simulation period in Euro
    :rtype: float
    """

    duration_sim_s = get_duration_of_simulation_period(power_grid_supply_list, timestep_s)

    # start value for commodity costs (variable gets updated with every timestep)
    commodity_costs_eur_sim = 0

    # create lists with energy supply per timestep and calculate costs:
    # factor 3600: kilo Joule --> kWh
    # factor 100: ct --> €
    for i in range(len(power_grid_supply_list)):
        energy_supply_per_timestep = (power_grid_supply_list[i] * timestep_s / 3600)  # [kWh]
        commodity_costs_eur_sim = (commodity_costs_eur_sim
                                   + (energy_supply_per_timestep * price_list[i] / 100))  # [€]
    commodity_costs_eur_per_year = commodity_costs_eur_sim * (duration_year_s / duration_sim_s)

    return commodity_costs_eur_per_year, commodity_costs_eur_sim


def calculate_capacity_costs_rlm(capacity_charge, max_power_strategy):
    """Calculates the capacity costs per year and simulation period for RLM customers
    :param capacity_charge: capacity charge from price sheet
    :type capacity_charge: float
    :param max_power_strategy: power for the calculation of the capacity costs (individual
    per strategy)
    :type max_power_strategy: float
    :return: capacity costs per year
    :rtype: float
    """

    capacity_costs_rlm_eur = capacity_charge * max_power_strategy  # [€]

    return capacity_costs_rlm_eur


def calculate_costs(strategy, voltage_level, interval_min, timestamps_list, power_grid_supply_list,
                    price_list, power_fix_load_list, power_feed_in_list, charging_signal_list,
                    core_standing_time_dict, price_sheet_json, results_json=None,
                    power_pv_nominal=0, duration_year_s=DURATION_YEAR_S):
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
    :param charging_signal_list: charging signal given by the distribution system operator
    (1: charge, 0: don't charge)
    :type charging_signal_list: list
    :param duration_year_s: duration of one year in seconds
    :type duration_year_s: int
    :return: total costs per year and simulation period (fees and taxes included)
    :rtype: float
    """

    # PRICE SHEET
    with open(price_sheet_json, "r", newline="") as ps:
        price_sheet = json.load(ps)

    # TEMPORAL PARAMETERS:
    timestep_s = interval_min * 60
    duration_sim_s = get_duration_of_simulation_period(timestamps_list, timestep_s)

    # ENERGY SUPPLY:
    energy_supply_sim = sum(power_grid_supply_list) * timestep_s / 3600
    energy_supply_per_year = energy_supply_sim * (duration_year_s / duration_sim_s)

    # COSTS FROM COMMODITY AND CAPACITY CHARGE DEPENDING ON CHARGING STRATEGY:
    if strategy in ["greedy", "balanced", "distributed"]:
        """
        Calculates costs in accordance with existing payment models.
        For SLP customers the variable capacity_charge is equivalent to the basic charge
        """

        # maximum power supplied from the grid:
        max_power_grid_supply = min(power_grid_supply_list)  # min() because of negative values [kW]

        # prices:
        utilization_time_per_year = abs(energy_supply_per_year / max_power_grid_supply)  # [h/a]
        commodity_charge, capacity_charge, fee_type = find_prices(
            price_sheet_json,
            strategy,
            voltage_level,
            utilization_time_per_year,
            energy_supply_per_year,
            UTILIZATION_TIME_PER_YEAR_EC
        )

        # CAPACITY COSTS:
        if fee_type == "SLP":
            capacity_costs_eur = -capacity_charge

        else:  # RLM
            capacity_costs_eur = \
                calculate_capacity_costs_rlm(capacity_charge, max_power_grid_supply)

        # COMMODITY COSTS:
        price_list = [commodity_charge] * len(power_grid_supply_list)
        commodity_costs_eur_per_year, commodity_costs_eur_sim = calculate_commodity_costs(
            price_list, power_grid_supply_list, timestep_s, duration_year_s)

    elif strategy == "balanced_market":
        """Payment model for the charging strategy 'balanced market'.
        For the charging strategy a price time series is used. The fix and flexible load are
        charged separately.
        Commodity and capacity costs fix: The price is depending on the utilization time per year
        (as usual). For the utilization time the maximum fix load and the fix energy supply per year
        is used. Then the fix costs are calculated as usual.
        Commodity and capacity costs flexible: For the flexible load all prices are based on the
        prices for a utilization time <2500 hours in the price sheet (prices for grid friendly power
        supply). For the commodity charge the generated price time series is adjusted to the prices
        for a utilization time >=2500 hours. Then the flexible commodity costs are calculated as
        usual. The flexible capacity costs are calculated only for grid supply in the high tariff
        window.
        """

        # COSTS FOR FIX LOAD

        # maximum fix power supplied from the grid [kW]:
        max_power_grid_supply_fix = min(power_fix_load_list)  # min() because of negative values

        if max_power_grid_supply_fix == 0:  # no fix load existing
            commodity_costs_eur_per_year_fix = 0
            commodity_costs_eur_sim_fix = 0
            capacity_costs_eur_fix = 0
        else:  # fix load existing
            # fix energy supply:
            energy_supply_sim_fix = sum(power_fix_load_list) * timestep_s / 3600
            energy_supply_per_year_fix = energy_supply_sim_fix * (duration_year_s / duration_sim_s)

            # prices:
            utilization_time_per_year_fix = abs(
                energy_supply_per_year_fix / max_power_grid_supply_fix)  # [h/a]
            commodity_charge_fix, capacity_charge_fix, fee_type = \
                find_prices(price_sheet_json, strategy, voltage_level,
                            utilization_time_per_year_fix, energy_supply_per_year_fix,
                            UTILIZATION_TIME_PER_YEAR_EC)

            # commodity costs for fix load:
            price_list_fix_load = [commodity_charge_fix] * len(power_fix_load_list)
            commodity_costs_eur_per_year_fix, commodity_costs_eur_sim_fix = \
                calculate_commodity_costs(price_list_fix_load, power_fix_load_list,
                                          timestep_s, duration_year_s)

            # capacity costs for fix load:
            capacity_costs_eur_fix = \
                calculate_capacity_costs_rlm(capacity_charge_fix, max_power_grid_supply_fix)

        # COSTS FOR FLEXIBLE LOAD

        power_flex_load_list = get_flexible_load(power_grid_supply_list, power_fix_load_list)

        # commodity charge used for comparison of tariffs (comp: compare):
        # The price time series for the flexible load in balanced market is based on the left
        # column of the price sheet. Consequently the prices from this column are needed for
        # the comparison of the tariffs.
        # set utilization_time for prices in left column
        utilization_time_per_year_comp = (UTILIZATION_TIME_PER_YEAR_EC - 1)
        commodity_charge_comp, capacity_charge_comp, fee_type_comp = \
            find_prices(price_sheet_json, strategy, voltage_level, utilization_time_per_year_comp,
                        energy_supply_per_year, UTILIZATION_TIME_PER_YEAR_EC)

        # adjust given price list (EUR/kWh --> ct/kWh)
        for i in range(len(price_list)):
            price_list[i] = price_list[i] * 100  # [ct/kWh]

        # low and medium tariff
        commodity_charge_lt = commodity_charge_comp * price_sheet[
            "strategy_related_cost_parameters"]["balanced_market"][
            "low_tariff_factor"]  # low tariff [€/kWh]
        commodity_charge_mt = commodity_charge_comp * price_sheet[
            "strategy_related_cost_parameters"][
            "balanced_market"]["medium_tariff_factor"]  # medium tariff [€/kWh]

        # find power at times of high tariff:
        power_flex_load_ht_list = []
        for i in range(len(power_flex_load_list)):
            if (price_list[i] > 0 and
                    (price_list[i] != commodity_charge_lt or price_list[i]) != commodity_charge_mt):
                power_flex_load_ht_list.append(power_flex_load_list[i])

        # maximum power for determination of capacity costs:
        # min() function because power drawn from the grid has a negative sign
        max_power_costs = min(power_flex_load_ht_list)

        # capacity costs for flexible load:
        # set a suitable utilization time in order to use prices for grid friendly charging
        utilization_time_per_year = UTILIZATION_TIME_PER_YEAR_EC
        commodity_charge_flex, capacity_charge_flex, fee_type = \
            find_prices(price_sheet_json, strategy, voltage_level, utilization_time_per_year,
                        energy_supply_per_year, UTILIZATION_TIME_PER_YEAR_EC)
        capacity_costs_eur_flex = \
            calculate_capacity_costs_rlm(capacity_charge_flex, max_power_costs)

        # price list for commodity charge for flexible load:
        ratio_commodity_charge = commodity_charge_flex / commodity_charge_comp
        for i in range(len(price_list)):
            price_list[i] = price_list[i] * ratio_commodity_charge  # [ct/kWh]

        # commodity costs for flexible load:
        commodity_costs_eur_per_year_flex, commodity_costs_eur_sim_flex = \
            calculate_commodity_costs(price_list, power_grid_supply_list,
                                      timestep_s, duration_year_s)

        # TOTAl COSTS:
        commodity_costs_eur_sim = commodity_costs_eur_sim_fix + commodity_costs_eur_sim_flex
        commodity_costs_eur_per_year = (commodity_costs_eur_per_year_fix
                                        + commodity_costs_eur_per_year_flex)
        capacity_costs_eur = capacity_costs_eur_fix + capacity_costs_eur_flex

    elif strategy == "flex_window":
        """Payment model for the charging strategy 'flex window'.
        The charging strategy uses a charging signal time series (1 = charge, 0 = don't charge).
        The fix and flexible load are charged separately.
        Commodity and capacity costs fix: The price is depending on the utilization time per year
        (as usual). For the
        utilization time the maximum fix load and the fix energy supply per year is used. Then the
        fix costs are calculated as usual.
        Commodity and capacity costs flexible: For the flexible load all prices are based on the
        right column of the price sheet (prices for grid friendly power supply). Then the flexible
        commodity costs are calculated as usual. The flexible capacity costs are calculated only for
        grid supply in the high tariff window (signal = 0).
        """

        # COSTS FOR FIX LOAD

        # maximum fix power supplied from the grid [kW]:
        max_power_grid_supply_fix = min(power_fix_load_list)  # min() because of negative values

        if max_power_grid_supply_fix == 0:  # no fix load existing
            commodity_costs_eur_per_year_fix = 0
            commodity_costs_eur_sim_fix = 0
            capacity_costs_eur_fix = 0
        else:  # fix load existing
            # fix energy supply:
            energy_supply_sim_fix = sum(power_fix_load_list) * timestep_s / 3600
            energy_supply_per_year_fix = energy_supply_sim_fix * (duration_year_s / duration_sim_s)

            # prices:
            utilization_time_per_year_fix = abs(
                energy_supply_per_year_fix / max_power_grid_supply_fix)  # [h/a]
            commodity_charge_fix, capacity_charge_fix, fee_type = \
                find_prices(price_sheet_json, strategy, voltage_level,
                            utilization_time_per_year_fix, energy_supply_per_year_fix,
                            UTILIZATION_TIME_PER_YEAR_EC)

            # commodity costs for fix load:
            price_list_fix_load = [commodity_charge_fix] * len(power_fix_load_list)
            commodity_costs_eur_per_year_fix, commodity_costs_eur_sim_fix = \
                calculate_commodity_costs(price_list_fix_load, power_fix_load_list,
                                          timestep_s, duration_year_s)

            # capacity costs for fix load:
            capacity_costs_eur_fix = \
                calculate_capacity_costs_rlm(capacity_charge_fix, max_power_grid_supply_fix)

        # COSTS FOR FLEXIBLE LOAD

        power_flex_load_list = get_flexible_load(power_grid_supply_list, power_fix_load_list)

        # prices:
        # set a suitable utilization time in order to use prices for grid friendly charging
        utilization_time_per_year_flex = UTILIZATION_TIME_PER_YEAR_EC
        commodity_charge_flex, capacity_charge_flex, fee_type = \
            find_prices(price_sheet_json, strategy, voltage_level, utilization_time_per_year_flex,
                        energy_supply_per_year, UTILIZATION_TIME_PER_YEAR_EC)

        # commodity costs for flexible load:
        price_list_flex_load = [commodity_charge_flex] * len(power_flex_load_list)
        commodity_costs_eur_per_year_flex, commodity_costs_eur_sim_flex = \
            calculate_commodity_costs(price_list_flex_load, power_flex_load_list,
                                      timestep_s, duration_year_s)

        # capacity costs for flexible load:
        power_flex_load_window_list = []
        for i in range(len(power_flex_load_list)):
            if charging_signal_list[i] == 0.0 and power_flex_load_list[i] < 0:
                power_flex_load_window_list.append(power_flex_load_list[i])
        # no flexible capacity costs if charging takes place only when signal = 1
        if power_flex_load_window_list == []:
            capacity_costs_eur_flex = 0
        else:
            max_power_grid_supply_flex = min(power_flex_load_window_list)
            capacity_costs_eur_flex = \
                calculate_capacity_costs_rlm(capacity_charge_flex, max_power_grid_supply_flex)

        # TOTAl COSTS:
        commodity_costs_eur_sim = commodity_costs_eur_sim_fix + commodity_costs_eur_sim_flex
        commodity_costs_eur_per_year = (commodity_costs_eur_per_year_fix
                                        + commodity_costs_eur_per_year_flex)
        capacity_costs_eur = capacity_costs_eur_fix + capacity_costs_eur_flex

    elif strategy == "schedule":
        """Payment model for the charging strategy 'schedule'.
        For the charging strategy a core standing time is chosen in which the distribution system
        operator can choose how the GC should draw power. The fix and flexible load are charged
        separately.
        Commodity and capacity costs fix: The price is depending on the utilization time per year
        (as usual). For the utilization time the maximum fix load and the energy supply per year
        for the fix load is used. The commodity charge can be lowered by a flat fee in order to
        reimburse flexibility. Then the fix commodity and capacity costs are calculated as usual.
        Commodity and capacity costs flexible: For the flexible load all prices are based on the
        right column of the price sheet (prices for grid friendly power supply). Then the flexible
        commodity costs are calculated as usual. The capacity costs for the flexible load are
        calculated for the times outside of the core standing time only.
        """

        # COSTS FOR FIX LOAD

        # maximum fix power supplied from the grid:
        max_power_grid_supply_fix = min(power_fix_load_list)  # minimum wegen negativen Werten [kW]

        if max_power_grid_supply_fix == 0:  # no fix load existing
            commodity_costs_eur_per_year_fix = 0
            commodity_costs_eur_sim_fix = 0
            capacity_costs_eur_fix = 0
        else:  # fix load existing
            # fix energy supply:
            energy_supply_sim_fix = sum(power_fix_load_list) * timestep_s / 3600
            energy_supply_per_year_fix = energy_supply_sim_fix * (duration_year_s / duration_sim_s)

            # prices
            utilization_time_per_year_fix = abs(
                energy_supply_per_year_fix / max_power_grid_supply_fix)  # [h/a]
            commodity_charge_fix, capacity_charge_fix, fee_type = \
                find_prices(price_sheet_json, strategy, voltage_level,
                            utilization_time_per_year_fix, energy_supply_per_year_fix,
                            UTILIZATION_TIME_PER_YEAR_EC)
            reduction_commodity_charge = price_sheet["strategy_related_cost_parameters"][
                "schedule"]["reduction_of_commodity_charge"]
            commodity_charge_fix = commodity_charge_fix - reduction_commodity_charge

            # commodity costs for fix load:
            price_list_fix_load = [commodity_charge_fix, ] * len(power_fix_load_list)
            commodity_costs_eur_per_year_fix, commodity_costs_eur_sim_fix = \
                calculate_commodity_costs(price_list_fix_load, power_fix_load_list,
                                          timestep_s, duration_year_s)

            # capacity costs for fix load:
            max_power_grid_supply_fix = min(power_fix_load_list)
            capacity_costs_eur_fix \
                = calculate_capacity_costs_rlm(capacity_charge_fix, max_power_grid_supply_fix)

        # COSTS FOR FLEXIBLE LOAD

        # power of flexible load:
        power_flex_load_list = get_flexible_load(power_grid_supply_list, power_fix_load_list)

        # prices:
        # set a suitable utilization time in order to use prices for grid friendly charging
        utilization_time_per_year_flex = UTILIZATION_TIME_PER_YEAR_EC
        commodity_charge_flex, capacity_charge_flex, fee_type = \
            find_prices(price_sheet_json, strategy, voltage_level, utilization_time_per_year_flex,
                        energy_supply_per_year, UTILIZATION_TIME_PER_YEAR_EC)

        # commodity costs for flexible load:
        price_list_flex_load = [commodity_charge_flex] * len(power_flex_load_list)
        commodity_costs_eur_per_year_flex, commodity_costs_eur_sim_flex = \
            calculate_commodity_costs(price_list_flex_load, power_flex_load_list,
                                      timestep_s, duration_year_s)

        # capacity costs for flexible load:
        power_outside_core_standing_time_flex_list = []
        # find times of grid supply outside of core standing time
        for i in range(len(timestamps_list)):
            # not within core standing time (cst)
            if (not dt_within_core_standing_time(timestamps_list[i], core_standing_time_dict) and
                    power_flex_load_list[i] < 0):
                power_outside_core_standing_time_flex_list.append(power_flex_load_list[i])
        # charging only within core standing time (cst)
        if power_outside_core_standing_time_flex_list == []:
            max_power_grid_supply_outside_cst_flex = 0
        else:
            max_power_grid_supply_outside_cst_flex = min(power_outside_core_standing_time_flex_list)

        capacity_costs_eur_flex = \
            calculate_capacity_costs_rlm(capacity_charge_flex,
                                         max_power_grid_supply_outside_cst_flex)

        # TOTAl COSTS:
        commodity_costs_eur_sim = commodity_costs_eur_sim_fix + commodity_costs_eur_sim_flex
        commodity_costs_eur_per_year = (commodity_costs_eur_per_year_fix
                                        + commodity_costs_eur_per_year_flex)
        capacity_costs_eur = capacity_costs_eur_fix + capacity_costs_eur_flex

    # COSTS NOT RELATED TO STRATEGIES

    # ADDITIONAL COSTS FOR RLM-CONSUMERS:
    if fee_type == "RLM":
        additional_costs_per_year = price_sheet["grid_fee"]["RLM"]["additional_costs"]["costs"]
        additional_costs_sim = additional_costs_per_year * (duration_sim_s / duration_year_s)
    else:
        additional_costs_per_year = 0
        additional_costs_sim = 0

    # COSTS FOR POWER PROCUREMENT:
    power_procurement_charge = price_sheet["power_procurement"]["charge"]  # [ct/kWh]
    power_procurement_costs_sim = (power_procurement_charge * energy_supply_sim / 100)  # [EUR]
    power_procurement_costs_per_year = (power_procurement_costs_sim
                                        * (duration_year_s / duration_sim_s))  # [EUR]

    # COSTS FROM LEVIES:

    # prices:
    eeg_levy = price_sheet["levies"]["EEG-levy"]  # [ct/kWh]
    chp_levy = price_sheet["levies"]["chp_levy"]  # [ct/kWh], chp: combined heat and power
    individual_charge_levy = price_sheet["levies"]["individual_charge_levy"]  # [ct/kWh]
    offshore_levy = price_sheet["levies"]["offshore_levy"]  # [ct/kWh]
    interruptible_loads_levy = price_sheet["levies"]["interruptible_loads_levy"]  # [ct/kWh]

    # costs for simulation_period:
    eeg_costs_sim = eeg_levy * energy_supply_sim / 100  # [EUR]
    chp_costs_sim = chp_levy * energy_supply_sim / 100  # [EUR]
    individual_charge_costs_sim = (individual_charge_levy * energy_supply_sim / 100)  # [EUR]
    offshore_costs_sim = offshore_levy * energy_supply_sim / 100  # [EUR]
    interruptible_loads_costs_sim = (interruptible_loads_levy * energy_supply_sim / 100)  # [EUR]
    levies_costs_total_sim = (
        eeg_costs_sim
        + chp_costs_sim
        + individual_charge_costs_sim
        + offshore_costs_sim
        + interruptible_loads_costs_sim
    )

    # costs per year:
    eeg_costs_per_year = eeg_costs_sim * (duration_year_s / duration_sim_s)  # [EUR]
    chp_costs_per_year = chp_costs_sim * (duration_year_s / duration_sim_s)  # [EUR]
    individual_charge_costs_per_year = (individual_charge_costs_sim
                                        * (duration_year_s / duration_sim_s))  # [EUR]
    offshore_costs_per_year = offshore_costs_sim * (duration_year_s / duration_sim_s)  # [EUR]
    interruptible_loads_costs_per_year = (interruptible_loads_costs_sim
                                          * (duration_year_s / duration_sim_s))  # [EUR]

    # COSTS FROM CONCESSION FEE:

    concession_fee = price_sheet["concession_fee"]["charge"]  # [ct/kWh]
    concession_fee_costs_sim = concession_fee * energy_supply_sim / 100  # [EUR]
    concession_fee_costs_per_year = (concession_fee_costs_sim
                                     * (duration_year_s / duration_sim_s))  # [EUR]

    # COSTS FROM FEED-IN REMUNERATION:

    # get nominal power of pv power plant:

    # PV power plant existing:
    if power_pv_nominal != 0:
        # find charge for PV remuneration depending on nominal power pf PV plant:
        pv_nominal_power_max = price_sheet["feed-in_remuneration"]["PV"]["kWp"]
        pv_remuneration = price_sheet["feed-in_remuneration"]["PV"]["remuneration"]
        if power_pv_nominal <= pv_nominal_power_max[0]:
            feed_in_charge_pv = pv_remuneration[0]  # [ct/kWh]
        elif power_pv_nominal <= pv_nominal_power_max[1]:
            feed_in_charge_pv = pv_remuneration[1]  # [ct/kWh]
        elif power_pv_nominal <= pv_nominal_power_max[2]:
            feed_in_charge_pv = pv_remuneration[2]  # [ct/kWh]
        else:
            raise ValueError("Feed-in remuneration for PV is calculated only for nominal powers of "
                             "up to 100 kWp")
    # PV power plant not existing:
    else:
        print('else')
        feed_in_charge_pv = 0  # [ct/kWh]

    # energy feed in by PV power plant:
    energy_feed_in_pv_sim = sum(power_feed_in_list) * timestep_s / 3600  # [kWh]
    energy_feed_in_pv_per_year = energy_feed_in_pv_sim * (duration_year_s / duration_sim_s)  # [kWh]

    # costs for PV feed-in:
    pv_feed_in_costs_sim = energy_feed_in_pv_sim * feed_in_charge_pv / 100  # [EUR]
    pv_feed_in_costs_per_year = energy_feed_in_pv_per_year * feed_in_charge_pv / 100  # [EUR]

    # COSTS FROM TAXES AND TOTAL COSTS:

    # electricity tax:
    electricity_tax = price_sheet["taxes"]["tax_on_electricity"]  # [ct/kWh]
    electricity_tax_costs_sim = electricity_tax * energy_supply_sim / 100  # [EUR]
    electricity_tax_costs_per_year = (electricity_tax_costs_sim
                                      * (duration_year_s / duration_sim_s))  # [EUR]

    # value added tax:
    value_added_tax_percent = price_sheet["taxes"]["value_added_tax"]  # [%]
    value_added_tax = value_added_tax_percent / 100  # [-]

    # total costs without value added tax (commodity costs and capacity costs included):
    costs_total_not_value_added_eur_sim = (
        commodity_costs_eur_sim
        + capacity_costs_eur
        + power_procurement_costs_sim
        + additional_costs_sim
        + levies_costs_total_sim
        + concession_fee_costs_sim
        + electricity_tax_costs_sim
    )  # [EUR]
    costs_total_not_value_added_eur_per_year = (costs_total_not_value_added_eur_sim
                                                * (duration_year_s / duration_sim_s))  # [EUR]

    # costs from value added tax:
    value_added_tax_costs_sim = value_added_tax * costs_total_not_value_added_eur_sim
    value_added_tax_costs_per_year = value_added_tax * costs_total_not_value_added_eur_per_year

    # total costs with value added tax (commodity costs and capacity costs included):
    costs_total_value_added_eur_sim = (costs_total_not_value_added_eur_sim
                                       + value_added_tax_costs_sim)
    costs_total_value_added_eur_per_year = (costs_total_not_value_added_eur_per_year
                                            + value_added_tax_costs_per_year)

    # WRITE ALL COSTS INTO JSON:

    if results_json is not None:

        capacity_or_basic_costs = "capacity costs"

        if strategy in ["greedy", "balanced", "distributed"]:
            # strategies without differentiation between fixed and flexible load
            information_fix_flex = "no differentiation between fix and flexible load"
            commodity_costs_eur_per_year_fix = information_fix_flex
            commodity_costs_eur_sim_fix = information_fix_flex
            capacity_costs_eur_fix = information_fix_flex
            commodity_costs_eur_per_year_flex = information_fix_flex
            commodity_costs_eur_sim_flex = information_fix_flex
            capacity_costs_eur_flex = information_fix_flex
            if fee_type == 'SLP':
                capacity_or_basic_costs = "basic costs"

        json_results_costs = {}
        round_to_places = 2

        json_results_costs["Costs"] = {
            "electricity costs": {
                "per year": {
                    "total (brutto)": round(costs_total_value_added_eur_per_year
                                            + pv_feed_in_costs_per_year, round_to_places),
                    "grid_fee": {
                        "total grid fee": round(commodity_costs_eur_per_year
                                                + capacity_costs_eur, round_to_places),
                        "commodity costs": {
                            "total costs": round(commodity_costs_eur_per_year, round_to_places),
                            "costs for fix load": commodity_costs_eur_per_year_fix,
                            "costs for flexible load": commodity_costs_eur_per_year_flex,
                        },
                        "capacity_or_basic_costs": {
                            "total costs": round(capacity_costs_eur, round_to_places),
                            "costs for fix load": capacity_costs_eur_fix,
                            "costs for flexible load": capacity_costs_eur_flex,
                        },
                        "additional costs": round(additional_costs_per_year, round_to_places),
                    },
                    "power procurement": round(power_procurement_costs_per_year, round_to_places),
                    "levies": {
                        "EEG-levy": round(eeg_costs_per_year, round_to_places),
                        "chp levy": round(chp_costs_per_year, round_to_places),
                        "individual charge levy": round(individual_charge_costs_per_year,
                                                        round_to_places),
                        "Offshore levy": round(offshore_costs_per_year, round_to_places),
                        "interruptible loads levy": round(interruptible_loads_costs_per_year,
                                                          round_to_places),
                    },
                    "concession fee": round(concession_fee_costs_per_year, round_to_places),
                    "taxes": {
                        "value added tax": round(value_added_tax_costs_per_year, round_to_places),
                        "tax on electricity": round(electricity_tax_costs_per_year, round_to_places)
                    },
                    "feed-in remuneration": {
                        "PV": round(pv_feed_in_costs_per_year, round_to_places),
                        "V2G": "to be implemented"
                    },
                    "unit": "EUR",
                    "info": "energy costs for one year",
                },
                "for simulation period": {
                    "total (brutto)": round(costs_total_value_added_eur_sim
                                            + pv_feed_in_costs_sim, round_to_places),
                    "grid fee": {
                        "total grid fee": round(commodity_costs_eur_sim
                                                + capacity_costs_eur, round_to_places),
                        "commodity costs": {
                            "total costs": round(commodity_costs_eur_sim, round_to_places),
                            "costs for fix load": commodity_costs_eur_sim_fix,
                            "costs for flexible load": commodity_costs_eur_sim_flex,
                        },
                        capacity_or_basic_costs: {
                            "total costs": round(capacity_costs_eur, round_to_places),
                            "costs for fix load": capacity_costs_eur_fix,
                            "costs for flexible load": capacity_costs_eur_flex,
                        },
                        "additional costs": round(additional_costs_sim, round_to_places),
                    },
                    "power procurement": round(power_procurement_costs_sim, round_to_places),
                    "levies": {
                        "EEG-levy": round(eeg_costs_sim, round_to_places),
                        "chp levy": round(chp_costs_sim, round_to_places),
                        "individual charge levy": round(individual_charge_costs_sim,
                                                        round_to_places),
                        "Offshore levy": round(offshore_costs_sim, round_to_places),
                        "interruptible loads levy": round(interruptible_loads_costs_sim,
                                                          round_to_places),
                    },
                    "concession fee": round(concession_fee_costs_sim, round_to_places),
                    "taxes": {
                        "value added tax": round(value_added_tax_costs_sim, round_to_places),
                        "tax on electricity": round(electricity_tax_costs_sim, round_to_places),
                    },
                    "feed-in remuneration": {
                        "PV": round(pv_feed_in_costs_sim, round_to_places),
                        "V2G": "to be implemented"
                    },
                    "unit": "EUR",
                    "info": "energy costs for simulation period",
                },
            }
        }

        # add dictionary to json with simulation data:
        with open(results_json, "r+", newline="") as sj:
            simulation_json = json.load(sj)
            sj.seek(0)
            simulation_json.update(json_results_costs)
            json.dump(simulation_json, sj, indent=2)

    # OUTPUT FOR SIMULATE.PY:

    total_costs_per_year = round(costs_total_value_added_eur_per_year
                                 + pv_feed_in_costs_per_year, round_to_places)

    commodity_costs_eur_per_year = round(commodity_costs_eur_per_year, round_to_places)
    capacity_costs_eur = round(capacity_costs_eur, round_to_places)

    power_procurement_per_year = round(power_procurement_costs_sim, round_to_places)

    levies_fees_and_taxes_per_year = round(eeg_costs_per_year, round_to_places)\
        + round(chp_costs_per_year, round_to_places)\
        + round(individual_charge_costs_per_year, round_to_places)\
        + round(offshore_costs_per_year, round_to_places)\
        + round(interruptible_loads_costs_per_year, round_to_places)\
        + round(concession_fee_costs_per_year, round_to_places)\
        + round(electricity_tax_costs_per_year, round_to_places)\
        + round(value_added_tax_costs_per_year, round_to_places)

    feed_in_remuneration_per_year = round(pv_feed_in_costs_per_year, round_to_places)

    return (total_costs_per_year,
            commodity_costs_eur_per_year,
            capacity_costs_eur,
            power_procurement_per_year,
            levies_fees_and_taxes_per_year,
            feed_in_remuneration_per_year)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='Generate scenarios as JSON files for vehicle charging modelling')
    parser.add_argument('--voltage_level', '-vl', help='Choose voltage level for cost calculation')
    parser.add_argument('--pv_power', type=int, default=0, help='set nominal power for local '
                                                                'photovoltaic power plant in kWp')
    parser.add_argument('--get_timeseries', '-ts', help='get timeseries from csv file.')
    parser.add_argument('--get_results', '-r', help='get simulation results from json file.')
    parser.add_argument('--get_cost_parameters', '-cp', help='get cost parameters from json file.')
    parser.add_argument('--config', help='Use config file to set arguments')

    args = parser.parse_args()

    set_options_from_config(args, check=False, verbose=False)

    # load simulation results:
    with open(args.get_results, "r", newline="") as sj:
        simulation_json = json.load(sj)

    # strategy:
    strategy = simulation_json["charging_strategy"]["strategy"]

    # simulation interval in minutes:
    interval_min = simulation_json["temporal_parameters"]["interval"]

    # core standing time for fleet:
    core_standing_time_dict = simulation_json.get("core_standing_time")

    # load simulation time series:
    (
        timestamps_list,
        price_list,
        power_grid_supply_list,
        power_feed_in_list,
        power_fix_load_list,
        charging_signal_list,
    ) = read_simulation_csv(args.get_timeseries, strategy)

    # voltage level of grid connection:
    voltage_level = args.voltage_level or simulation_json.get("grid_connector",
                                                              {}).get("voltage_level")
    if voltage_level is None:
        raise Exception("voltage")
    print(voltage_level)

    # cost calculation:
    calculate_costs(
        strategy,
        voltage_level,
        interval_min,
        timestamps_list,
        power_grid_supply_list,
        price_list,
        power_fix_load_list,
        power_feed_in_list,
        charging_signal_list,
        core_standing_time_dict,
        args.get_cost_parameters,
        args.get_results,
        args.pv_power
    )
