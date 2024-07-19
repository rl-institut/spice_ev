import json
import datetime
import warnings

# constants for grid fee

# constant utilization time of the grid needed in order to find commodity and capacity charge
# in the price sheet:
UTILIZATION_TIME_PER_YEAR_EC = 2500  # ec: edge condition, [h/a]

# maximum yearly energy supply for SLP customers:
MAX_ENERGY_SUPPLY_PER_YEAR_SLP = 100000


def get_flexible_load(power_grid_supply_list, power_fix_load_list):
    """ Determine power of flexible load.

    :param power_grid_supply_list: power supplied from the power grid
    :type power_grid_supply_list: list
    :param power_fix_load_list: power of the fixed load
    :type power_fix_load_list: list
    :return: power of flexible load in kW
    :rtype: list
    """

    power_flex_load_list = [max(supply - power_fix_load_list[i], 0) for i, supply in
                            enumerate(power_grid_supply_list)]

    return power_flex_load_list


def find_prices(price_sheet, strategy, voltage_level, utilization_time_per_year,
                energy_supply_per_year, utilization_time_per_year_ec=UTILIZATION_TIME_PER_YEAR_EC):
    """ Read commodity and capacity charge from price sheets.

    For type 'SLP' the capacity charge is equivalent to the basic charge.

    :param price_sheet: price sheet of grid operator
    :type price_sheet: dict
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

    energy_below_slp = abs(energy_supply_per_year) <= MAX_ENERGY_SUPPLY_PER_YEAR_SLP
    if strategy in ["greedy", "balanced", "distributed", "variable_costs"] and energy_below_slp:
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


def calculate_commodity_costs(price_list, power_supply_list, interval, fraction_year):
    """ Calculate commodity costs for all types of customers.

    :param price_list: price list with commodity charge per timestamp
    :type price_list: list
    :param power_supply_list: power supplied from the grid
    :type power_supply_list: list
    :param interval: simulation interval
    :type interval: timedelta
    :param fraction_year: simulation time relative to one year
    :type fraction_year: float
    :return: commodity costs per year and simulation period in Euro
    :rtype: float
    """

    # start value for commodity costs (variable gets updated with every timestep)
    commodity_costs_eur_sim = 0

    # create lists with energy supply per timestep and calculate costs:
    # factor 3600: kilo Joule --> kWh
    # factor 100: ct --> €
    for i in range(len(power_supply_list)):
        energy_supply_per_timestep = (
            power_supply_list[i] * interval.total_seconds() / 3600)  # [kWh]
        commodity_costs_eur_sim += energy_supply_per_timestep * price_list[i] / 100  # [€]
    commodity_costs_eur_per_year = commodity_costs_eur_sim / fraction_year

    return commodity_costs_eur_per_year, commodity_costs_eur_sim


def calculate_capacity_costs_rlm(capacity_charge, max_power_strategy):
    """ Calculate the capacity costs per year and simulation period for RLM customers.

    :param capacity_charge: capacity charge from price sheet
    :type capacity_charge: float
    :param max_power_strategy: power for the calculation of the capacity costs
    :type max_power_strategy: float
    :return: capacity costs per year in Euro
    :rtype: float
    """

    return capacity_charge * max_power_strategy  # [€]


def calculate_feed_in_remuneration(feed_in_charge, power_feed_in_list,
                                   timestamps_list, interval, fraction_year):
    """ Calculate the feed-in remuneration per year and simulation period.

    :param feed_in_charge: feed-in charge
    :type feed_in_charge: float
    :param power_feed_in_list: power fed into the grid
    :type power_feed_in_list: list
    :param timestamps_list: timestamps of simulated points in time
    :type timestamps_list: list
    :param interval: simulation interval
    :type interval: timedelta
    :param fraction_year: simulation time relative to one year
    :type fraction_year: float
    :return: feed-in remuneration per year and simulation period in Euro
    :rtype: float
    """

    if power_feed_in_list is None:
        power_feed_in_list = [0] * len(timestamps_list)
    energy_feed_in_sim = sum(power_feed_in_list) * interval.total_seconds() / 3600  # [kWh]
    energy_feed_in_per_year = energy_feed_in_sim / fraction_year  # [kWh]

    # costs for PV feed-in:
    feed_in_costs_sim = energy_feed_in_sim * feed_in_charge / 100  # [EUR]
    feed_in_costs_per_year = energy_feed_in_per_year * feed_in_charge / 100  # [EUR]

    return feed_in_costs_per_year, feed_in_costs_sim


def calculate_costs(strategy, voltage_level, interval,
                    timestamps_list, power_grid_supply_list,
                    price_list, power_fix_load_list, power_generation_feed_in_list,
                    power_v2g_feed_in_list, power_battery_feed_in_list, charging_signal_list,
                    price_sheet_path, grid_operator="default_grid_operator", results_json=None,
                    power_pv_nominal=0, power_schedule_list=None):
    """Calculate costs for the chosen charging strategy.

    :param strategy: charging strategy
    :type strategy: str
    :param voltage_level: voltage level of the power grid the fleet is connected to
    :type voltage_level: str
    :param interval: duration of one simulation timestep
    :type interval: timedelta
    :param timestamps_list: timestamps from simulation
    :type timestamps_list: list
    :param power_grid_supply_list: power supplied from the grid
    :type power_grid_supply_list: list
    :param price_list: prices for energy supply in EUR/kWh
    :type price_list: list
    :param power_fix_load_list: power supplied from the grid for the fixed load
    :type power_fix_load_list: list
    :param power_generation_feed_in_list: power fed into the grid from local generation
    :type power_generation_feed_in_list: list
    :param power_v2g_feed_in_list: power fed into the grid from V2G
    :type power_v2g_feed_in_list: list
    :param power_battery_feed_in_list: power fed into the grid from battery
    :type power_battery_feed_in_list: list
    :param charging_signal_list: charging signal (True (1): charge, False (0): don't charge)
    :type charging_signal_list: list
    :param price_sheet_path: path to price sheet
    :type price_sheet_path: str
    :param grid_operator: grid operator of grid connection point (default: None)
    :type grid_operator: str
    :param results_json: path to resulting json
    :type results_json: str
    :param power_pv_nominal: nominal power of pv power plant
    :type power_pv_nominal: int
    :param power_schedule_list: power to be supplied or fed-in according to schedule
    :type power_schedule_list: list
    :raises Exception: if charging strategy is not supported
    :raises ValueError: if nom. PV power exceeds max. power for feed-in remuneration in price sheet
    :return: total costs per year and simulation period (fees and taxes included)
    :rtype: dict
    """

    # sanity checks
    assert voltage_level is not None, "Voltage level must be set for cost calculation"
    assert price_sheet_path is not None, "Price sheet must be given"

    # PRICE SHEET
    with open(price_sheet_path, "r", newline="") as ps:
        price_sheet = json.load(ps).get(grid_operator)
        assert bool(price_sheet)  # grid_operator section exists and is not empty

    # TEMPORAL PARAMETERS
    # fraction of scenario duration in relation to one year
    fraction_year = len(timestamps_list) * interval / datetime.timedelta(days=365)

    # extract actual grid supply (change sign)
    power_grid_supply_list = [max(-v, 0) for v in power_grid_supply_list]

    # only consider positive values of fixed load for cost calculation
    power_fix_load_list = [max(v, 0) for v in power_fix_load_list]

    if type(price_list) is dict:
        # price_list may contain procurement and commodity cost lists
        procurement_price_list = price_list.get('procurement')
        commodity_price_list = price_list['commodity']
    else:
        # default price list: interpret as commodity price list
        procurement_price_list = None
        commodity_price_list = price_list

    # ENERGY SUPPLY:
    energy_supply_sim = sum(power_grid_supply_list) * interval.total_seconds() / 3600
    energy_supply_per_year = energy_supply_sim / fraction_year

    # COSTS FROM COMMODITY AND CAPACITY CHARGE DEPENDING ON CHARGING STRATEGY:
    if strategy in [
            "greedy", "balanced", "distributed",
            "peak_shaving", "peak_load_window", "variable_costs"]:
        """
        Calculates costs in accordance with existing payment models.
        For SLP customers the variable capacity_charge is equivalent to the basic charge
        """

        # maximum power supplied from the grid:
        max_power_grid_supply = max(power_grid_supply_list + [0])

        # prices:
        if max_power_grid_supply == 0:
            utilization_time_per_year = 0
        else:
            utilization_time_per_year = abs(energy_supply_per_year / max_power_grid_supply)  # [h/a]
        commodity_charge, capacity_charge, fee_type = find_prices(
            price_sheet,
            strategy,
            voltage_level,
            utilization_time_per_year,
            energy_supply_per_year,
            UTILIZATION_TIME_PER_YEAR_EC
        )

        if strategy == "peak_load_window":
            # get peak power inside time windows
            if charging_signal_list is None:
                # no time windows: no window loads -> peak power is set to 0
                window_loads = []
            else:
                window_loads = [l for (l, w) in
                                zip(power_grid_supply_list, charging_signal_list) if w]
            peak_power_in_windows = max(window_loads + [0])
            # check if cost calculation for peak_load_window can be applied: significance_threshold
            significance_threshold = 0
            if max_power_grid_supply > 0:
                significance_threshold = (
                    (max_power_grid_supply - peak_power_in_windows) / max_power_grid_supply) * 100
            significance_threshold_price_sheet = (
                price_sheet["strategy_related"][strategy]["significance_threshold"][voltage_level])
            # check if cost model can be applied
            peak_diff = max_power_grid_supply - peak_power_in_windows
            if significance_threshold > significance_threshold_price_sheet and peak_diff > 100:
                # only use peak power inside windows for calculation of capacity costs
                max_power_grid_supply = peak_power_in_windows
            # save threshold from price sheet to update json file
            json_results_windows = {
                "significance threshold from price sheet": significance_threshold_price_sheet
            }

        # COMMODITY COSTS
        if strategy == "variable_costs":
            # virtual strategy, just for cost calculation
            # apply procurement and commodity costs for each timestep to grid supply
            # this is just drawn power, feed-in is handled independent of strategy
            ts_per_hour = interval.total_seconds() / 3600
            commodity_costs_eur_sim = 0
            power_procurement_costs_sim = 0
            if procurement_price_list is None:
                warnings.warn("Strategy variable_costs without procurement cost timeseries")
            for i, power in enumerate(power_grid_supply_list):
                energy_supply_per_timestep = (power * ts_per_hour)  # [kWh]
                commodity_costs_eur_sim += (
                    energy_supply_per_timestep * commodity_price_list[i] / 100)
                if procurement_price_list is not None:
                    power_procurement_costs_sim += (
                        energy_supply_per_timestep * procurement_price_list[i] / 100)
            commodity_costs_eur_per_year = commodity_costs_eur_sim / fraction_year
        else:
            # use fixed commodity charge
            price_list = [commodity_charge] * len(power_grid_supply_list)
            commodity_costs_eur_per_year, commodity_costs_eur_sim = calculate_commodity_costs(
                price_list, power_grid_supply_list, interval, fraction_year)

        # CAPACITY COSTS
        if fee_type == "SLP":
            capacity_costs_eur = capacity_charge
        else:  # RLM
            capacity_costs_eur = calculate_capacity_costs_rlm(
                capacity_charge, max_power_grid_supply)

    elif strategy == "balanced_market":
        """Payment model for the charging strategy 'balanced market'.
        For the charging strategy a price time series is used. The fixed and flexible load are
        charged separately.
        Commodity and capacity costs fixed: The price is depending on the utilization time per year
        (as usual). For the utilization time the maximum fixed load and the fixed energy supply per
        year is used. Then the fixed costs are calculated as usual.
        Commodity and capacity costs flexible: For the flexible load all prices are based on the
        prices for a utilization time >=2500 hours in the price sheet (prices for grid friendly
        power supply). Then the flexible commodity costs are calculated as usual. The flexible
        capacity costs are calculated only for grid supply in the high tariff window.
        """

        # COSTS FOR FIXED LOAD

        # maximum fixed power supplied from the grid [kW]:
        max_power_grid_supply_fix = max(power_fix_load_list + [0])

        if max_power_grid_supply_fix == 0:  # no fix load existing
            commodity_costs_eur_per_year_fix = 0
            commodity_costs_eur_sim_fix = 0
            capacity_costs_eur_fix = 0
        else:  # fixed load existing
            # fixed energy supply:
            energy_supply_sim_fix = sum(power_fix_load_list) * interval.total_seconds() / 3600
            energy_supply_per_year_fix = energy_supply_sim_fix / fraction_year

            # prices:
            utilization_time_per_year_fix = abs(
                energy_supply_per_year_fix / max_power_grid_supply_fix)  # [h/a]
            commodity_charge_fix, capacity_charge_fix, fee_type = find_prices(
                price_sheet,
                strategy,
                voltage_level,
                utilization_time_per_year_fix,
                energy_supply_per_year_fix,
                UTILIZATION_TIME_PER_YEAR_EC
            )

            # commodity costs for fixed load:
            price_list_fix_load = [commodity_charge_fix] * len(power_fix_load_list)
            commodity_costs_eur_per_year_fix, commodity_costs_eur_sim_fix = (
                calculate_commodity_costs(price_list_fix_load, power_fix_load_list,
                                          interval, fraction_year))

            # capacity costs for fixed load:
            capacity_costs_eur_fix = calculate_capacity_costs_rlm(
                capacity_charge_fix, max_power_grid_supply_fix)

        # COSTS FOR FLEXIBLE LOAD

        power_flex_load_list = get_flexible_load(power_grid_supply_list, power_fix_load_list)

        # adjust given price list (EUR/kWh --> ct/kWh)
        price_list = [price * 100 for price in commodity_price_list]

        # find power at times of high tariff
        max_price = max(price_list)
        max_power_high_tariff = 0
        for i, power in enumerate(power_flex_load_list):
            if (price_list[i] == max_price) and (power > max_power_high_tariff):
                max_power_high_tariff = power

        # capacity costs for flexible load:
        # set a suitable utilization time in order to use prices for grid friendly charging
        utilization_time_per_year = UTILIZATION_TIME_PER_YEAR_EC
        commodity_charge_flex, capacity_charge_flex, fee_type = find_prices(
            price_sheet,
            strategy,
            voltage_level,
            utilization_time_per_year,
            energy_supply_per_year,
            UTILIZATION_TIME_PER_YEAR_EC
        )
        capacity_costs_eur_flex = (
            calculate_capacity_costs_rlm(capacity_charge_flex, max_power_high_tariff))

        # commodity costs for flexible load:
        commodity_costs_eur_per_year_flex, commodity_costs_eur_sim_flex = calculate_commodity_costs(
            price_list, power_flex_load_list, interval, fraction_year)

        # TOTAl COSTS:
        commodity_costs_eur_sim = commodity_costs_eur_sim_fix + commodity_costs_eur_sim_flex
        commodity_costs_eur_per_year = (commodity_costs_eur_per_year_fix
                                        + commodity_costs_eur_per_year_flex)
        capacity_costs_eur = capacity_costs_eur_fix + capacity_costs_eur_flex

    elif strategy == "flex_window":
        """Payment model for the charging strategy 'flex window'.
        The charging strategy uses a charging signal time series (1 = charge, 0 = don't charge).
        The fixed and flexible load are charged separately.
        Commodity and capacity costs fixed: The price is depending on the utilization time per year
        (as usual). For the
        utilization time the maximum fixed load and the fixed energy supply per year is used. Then
        the fixed costs are calculated as usual.
        Commodity and capacity costs flexible: For the flexible load all prices are based on the
        prices for a grid utilization time >=2500 h of the price sheet (prices for grid friendly
        power supply). Then the flexible commodity costs are calculated as usual. The flexible
        capacity costs are calculated only for grid supply in the high tariff window (signal = 0).
        """

        # COSTS FOR FIXED LOAD

        # maximum fixed power supplied from the grid [kW]
        max_power_grid_supply_fix = max(power_fix_load_list + [0])

        if max_power_grid_supply_fix == 0:
            # no fix load existing
            commodity_costs_eur_per_year_fix = 0
            commodity_costs_eur_sim_fix = 0
            capacity_costs_eur_fix = 0
        else:
            # fixed load existing
            # fixed energy supply:
            energy_supply_sim_fix = sum(power_fix_load_list) * interval.total_seconds() / 3600
            energy_supply_per_year_fix = energy_supply_sim_fix / fraction_year

            # prices:
            utilization_time_per_year_fix = abs(
                energy_supply_per_year_fix / max_power_grid_supply_fix)  # [h/a]
            commodity_charge_fix, capacity_charge_fix, fee_type = find_prices(
                price_sheet,
                strategy,
                voltage_level,
                utilization_time_per_year_fix,
                energy_supply_per_year_fix,
                UTILIZATION_TIME_PER_YEAR_EC
            )

            # commodity costs for fixed load:
            price_list_fix_load = [commodity_charge_fix] * len(power_fix_load_list)
            commodity_costs_eur_per_year_fix, commodity_costs_eur_sim_fix = (
                calculate_commodity_costs(price_list_fix_load, power_fix_load_list,
                                          interval, fraction_year))

            # capacity costs for fixed load:
            capacity_costs_eur_fix = (
                calculate_capacity_costs_rlm(capacity_charge_fix, max_power_grid_supply_fix))

        # COSTS FOR FLEXIBLE LOAD

        power_flex_load_list = get_flexible_load(power_grid_supply_list, power_fix_load_list)

        # prices:
        # set a suitable utilization time in order to use prices for grid friendly charging
        utilization_time_per_year_flex = UTILIZATION_TIME_PER_YEAR_EC
        commodity_charge_flex, capacity_charge_flex, fee_type = find_prices(
            price_sheet,
            strategy,
            voltage_level,
            utilization_time_per_year_flex,
            energy_supply_per_year,
            UTILIZATION_TIME_PER_YEAR_EC
        )

        # commodity costs for flexible load:
        price_list_flex_load = [commodity_charge_flex] * len(power_flex_load_list)
        commodity_costs_eur_per_year_flex, commodity_costs_eur_sim_flex = (
            calculate_commodity_costs(price_list_flex_load, power_flex_load_list,
                                      interval, fraction_year))

        # capacity costs for flexible load:
        power_flex_load_window_list = []
        for i in range(len(power_flex_load_list)):
            if not charging_signal_list[i]:
                power_flex_load_window_list.append(power_flex_load_list[i])
        # no flexible capacity costs if charging takes place only when signal = 1
        if power_flex_load_window_list == []:
            capacity_costs_eur_flex = 0
        else:
            max_power_grid_supply_flex = max(power_flex_load_window_list)
            capacity_costs_eur_flex = (
                calculate_capacity_costs_rlm(capacity_charge_flex, max_power_grid_supply_flex))

        # TOTAl COSTS:
        commodity_costs_eur_sim = commodity_costs_eur_sim_fix + commodity_costs_eur_sim_flex
        commodity_costs_eur_per_year = (commodity_costs_eur_per_year_fix
                                        + commodity_costs_eur_per_year_flex)
        capacity_costs_eur = capacity_costs_eur_fix + capacity_costs_eur_flex

    elif strategy == "schedule":
        """Payment model for the charging strategy 'schedule'.
        The charging strategy follows a schedule set by the distribution system operator. The fixed
        and flexible load as well as the total deviation from schedule are charged separately.
        Commodity and capacity costs fixed: The price is depending on the utilization time per year
        (as usual). For the utilization time the maximum fixed load and the energy supply per year
        for the fixed load is used. The commodity charge can be lowered by a flat fee in order to
        reimburse flexibility. Then the fixed commodity and capacity costs are calculated as usual.
        Commodity costs flexible: For the flexible load the commodity charge is set according to
        the right column of the price sheet (prices for grid friendly power supply). Then the
        flexible commodity costs are calculated as usual. A capacity charge is not applied on the
        flexible load.
        Capacity related costs for deviation from schedule: A deviation charge is applied on the
        maximum positive deviation of the total load from the schedule.
        """

        # schedule related charges:
        schedule_charges = price_sheet["strategy_related"]["schedule"]

        # COSTS FOR FIXED LOAD

        # maximum fixed power supplied from the grid:
        max_power_grid_supply_fix = max(power_fix_load_list + [0])

        if max_power_grid_supply_fix == 0:
            # no fixed load existing
            commodity_costs_eur_per_year_fix = 0
            commodity_costs_eur_sim_fix = 0
            capacity_costs_eur_fix = 0
        else:
            # fixed load existing
            # fixed energy supply:
            energy_supply_sim_fix = sum(power_fix_load_list) * interval.total_seconds() / 3600
            energy_supply_per_year_fix = energy_supply_sim_fix / fraction_year

            # prices
            utilization_time_per_year_fix = abs(
                energy_supply_per_year_fix / max_power_grid_supply_fix)  # [h/a]
            commodity_charge_fix, capacity_charge_fix, fee_type = find_prices(
                price_sheet,
                strategy,
                voltage_level,
                utilization_time_per_year_fix,
                energy_supply_per_year_fix,
                UTILIZATION_TIME_PER_YEAR_EC
            )
            reduction_commodity_charge = schedule_charges["reduction_of_commodity_charge"]
            commodity_charge_fix = commodity_charge_fix - reduction_commodity_charge

            # commodity costs for fixed load:
            price_list_fix_load = [commodity_charge_fix, ] * len(power_fix_load_list)
            commodity_costs_eur_per_year_fix, commodity_costs_eur_sim_fix = (
                calculate_commodity_costs(price_list_fix_load, power_fix_load_list,
                                          interval, fraction_year))

            # capacity costs for fixed load:
            capacity_costs_eur_fix = calculate_capacity_costs_rlm(
                capacity_charge_fix, max_power_grid_supply_fix)

        # COSTS FOR FLEXIBLE LOAD

        # power of flexible load:
        power_flex_load_list = get_flexible_load(power_grid_supply_list, power_fix_load_list)

        # prices:
        # set a suitable utilization time in order to use prices for grid friendly charging
        utilization_time_per_year_flex = UTILIZATION_TIME_PER_YEAR_EC
        commodity_charge_flex, capacity_charge_flex, fee_type = find_prices(
            price_sheet,
            strategy,
            voltage_level,
            utilization_time_per_year_flex,
            energy_supply_per_year,
            UTILIZATION_TIME_PER_YEAR_EC
        )

        # commodity costs for flexible load:
        price_list_flex_load = [commodity_charge_flex] * len(power_flex_load_list)
        commodity_costs_eur_per_year_flex, commodity_costs_eur_sim_flex = (
            calculate_commodity_costs(price_list_flex_load, power_flex_load_list,
                                      interval, fraction_year))

        # DEVIATION COSTS:

        if power_schedule_list is None:
            warnings.warn("No schedule is given")
            capacity_costs_eur_flex = 0.0
        else:
            # positive deviation from schedule concerning grid supply (not feed-in):
            power_grid_supply_schedule_list = [max(v, 0) for v in power_schedule_list]
            pos_deviation_grid_supply_list = [max(power_grid_supply_list[i] - schedule_power, 0)
                                              for i, schedule_power
                                              in enumerate(power_grid_supply_schedule_list)]

            # charge for deviation from schedule:
            schedule_deviation_charge = schedule_charges["deviation_charge"]

            # tolerance for charging deviation from schedule:
            schedule_deviation_tolerance = schedule_charges["deviation_tolerance"]

            # capacity related costs for deviation from schedule:
            max_pos_deviation_grid_supply = max(pos_deviation_grid_supply_list)
            max_grid_supply_schedule = max(power_grid_supply_schedule_list)
            lower_limit_deviation = max_grid_supply_schedule * schedule_deviation_tolerance

            charged_deviation_power = max(max_pos_deviation_grid_supply - lower_limit_deviation, 0)

            capacity_costs_eur_flex = calculate_capacity_costs_rlm(
                schedule_deviation_charge, charged_deviation_power)

        # TOTAL COSTS:
        commodity_costs_eur_sim = commodity_costs_eur_sim_fix + commodity_costs_eur_sim_flex
        commodity_costs_eur_per_year = (commodity_costs_eur_per_year_fix
                                        + commodity_costs_eur_per_year_flex)
        capacity_costs_eur = capacity_costs_eur_fix + capacity_costs_eur_flex
    else:
        raise Exception(f"Cost calculation does not support charging strategy {str(strategy)}")

    # COSTS NOT RELATED TO STRATEGIES

    # ADDITIONAL COSTS FOR RLM-CONSUMERS:
    if fee_type == "RLM":
        additional_costs_per_year = price_sheet["grid_fee"]["RLM"]["additional_costs"]["costs"]
        additional_costs_sim = additional_costs_per_year * fraction_year
    else:
        additional_costs_per_year = 0
        additional_costs_sim = 0

    # COSTS FOR POWER PROCUREMENT:
    if strategy != "variable_costs":
        power_procurement_charge = price_sheet["power_procurement"]["charge"]  # [ct/kWh]
        power_procurement_costs_sim = (power_procurement_charge * energy_supply_sim / 100)  # [EUR]
    power_procurement_costs_per_year = (power_procurement_costs_sim / fraction_year)  # [EUR]

    # COSTS FROM LEVIES:

    # prices [ct/kWh]:
    eeg_levy = price_sheet["levies"]["EEG_levy"]
    chp_levy = price_sheet["levies"]["chp_levy"]
    individual_charge_levy = price_sheet["levies"]["individual_charge_levy"]
    offshore_levy = price_sheet["levies"]["offshore_levy"]
    interruptible_loads_levy = price_sheet["levies"]["interruptible_loads_levy"]

    # costs for simulation_period [EUR]:
    eeg_costs_sim = eeg_levy * energy_supply_sim / 100
    chp_costs_sim = chp_levy * energy_supply_sim / 100
    individual_charge_costs_sim = (individual_charge_levy * energy_supply_sim / 100)
    offshore_costs_sim = offshore_levy * energy_supply_sim / 100
    interruptible_loads_costs_sim = (interruptible_loads_levy * energy_supply_sim / 100)
    levies_costs_total_sim = (
        eeg_costs_sim
        + chp_costs_sim
        + individual_charge_costs_sim
        + offshore_costs_sim
        + interruptible_loads_costs_sim
    )

    # costs per year [EUR]:
    eeg_costs_per_year = eeg_costs_sim / fraction_year
    chp_costs_per_year = chp_costs_sim / fraction_year
    individual_charge_costs_per_year = individual_charge_costs_sim / fraction_year
    offshore_costs_per_year = offshore_costs_sim / fraction_year
    interruptible_loads_costs_per_year = interruptible_loads_costs_sim / fraction_year

    # COSTS FROM CONCESSION FEE:
    concession_fee = price_sheet["concession_fee"]["charge"]  # [ct/kWh]
    concession_fee_costs_sim = concession_fee * energy_supply_sim / 100  # [EUR]
    concession_fee_costs_per_year = concession_fee_costs_sim / fraction_year  # [EUR]

    # COSTS FROM FEED-IN REMUNERATION:

    # feed-in remuneration PV:

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
                             f"up to {pv_nominal_power_max[-1]} kWp")
    # PV power plant not existing:
    else:
        feed_in_charge_pv = 0  # [ct/kWh]
        if power_generation_feed_in_list is not None and sum(power_generation_feed_in_list) != 0:
            warnings.warn("Nominal power of PV power plant is zero even though there is an "
                          "existing generation time series")

    # remuneration for PV feed-in:
    pv_feed_in_costs_per_year, pv_feed_in_costs_sim = calculate_feed_in_remuneration(
        feed_in_charge_pv, power_generation_feed_in_list, timestamps_list, interval, fraction_year)

    # feed-in remuneration V2G:

    # charge for V2G remuneration:
    feed_in_charge_v2g = price_sheet["feed-in_remuneration"]["V2G"]
    # remuneration for V2G feed-in:
    v2g_feed_in_costs_per_year, v2g_feed_in_costs_sim = calculate_feed_in_remuneration(
        feed_in_charge_v2g, power_v2g_feed_in_list, timestamps_list, interval, fraction_year)

    # feed-in remuneration battery:

    # charge for battery feed-in remuneration:
    feed_in_charge_battery = price_sheet["feed-in_remuneration"]["battery"]
    # remuneration for battery feed-in:
    battery_feed_in_costs_per_year, battery_feed_in_costs_sim = calculate_feed_in_remuneration(
        feed_in_charge_battery, power_battery_feed_in_list, timestamps_list, interval,
        fraction_year)

    # COSTS FROM TAXES AND TOTAL COSTS:

    # electricity tax:
    electricity_tax = price_sheet["taxes"]["tax_on_electricity"]  # [ct/kWh]
    electricity_tax_costs_sim = electricity_tax * energy_supply_sim / 100  # [EUR]
    electricity_tax_costs_per_year = electricity_tax_costs_sim / fraction_year  # [EUR]

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

    # total costs for year (capacity costs are always given per year)
    costs_total_not_value_added_eur_per_year = ((costs_total_not_value_added_eur_sim -
                                                 capacity_costs_eur) / fraction_year +
                                                capacity_costs_eur)

    # costs from value added tax:
    value_added_tax_costs_sim = value_added_tax * costs_total_not_value_added_eur_sim
    value_added_tax_costs_per_year = value_added_tax * costs_total_not_value_added_eur_per_year

    # total costs with value added tax (commodity costs and capacity costs included):
    costs_total_value_added_eur_sim = (costs_total_not_value_added_eur_sim
                                       + value_added_tax_costs_sim)
    costs_total_value_added_eur_per_year = (costs_total_not_value_added_eur_per_year
                                            + value_added_tax_costs_per_year)

    # aggregated and rounded values
    round_to_places = 2
    total_costs_sim = round(
        costs_total_value_added_eur_sim
        - pv_feed_in_costs_sim
        - v2g_feed_in_costs_sim
        - battery_feed_in_costs_sim,
        round_to_places)
    total_costs_per_year = round(
        costs_total_value_added_eur_per_year
        - pv_feed_in_costs_per_year
        - v2g_feed_in_costs_per_year
        - battery_feed_in_costs_per_year,
        round_to_places)

    commodity_costs_eur_per_year = round(commodity_costs_eur_per_year, round_to_places)
    capacity_costs_eur = round(capacity_costs_eur, round_to_places)

    power_procurement_costs_sim = round(power_procurement_costs_sim, round_to_places)
    power_procurement_costs_per_year = round(power_procurement_costs_per_year, round_to_places)

    levies_fees_and_taxes_per_year = round(
        round(eeg_costs_per_year, round_to_places) +
        round(chp_costs_per_year, round_to_places) +
        round(individual_charge_costs_per_year, round_to_places) +
        round(offshore_costs_per_year, round_to_places) +
        round(interruptible_loads_costs_per_year, round_to_places) +
        round(concession_fee_costs_per_year, round_to_places) +
        round(electricity_tax_costs_per_year, round_to_places) +
        round(value_added_tax_costs_per_year, round_to_places),
        round_to_places)

    feed_in_remuneration_per_year = round(
        pv_feed_in_costs_per_year
        + v2g_feed_in_costs_per_year
        + battery_feed_in_costs_per_year,
        round_to_places)

    # WRITE ALL COSTS INTO JSON:
    if results_json is not None:

        capacity_or_basic_costs = "capacity costs"

        if strategy in ["greedy", "balanced", "distributed", "peak_shaving", "peak_load_window"]:
            # strategies without differentiation between fixed and flexible load
            information_fix_flex = "no differentiation between fixed and flexible load"
            commodity_costs_eur_per_year_fix = information_fix_flex
            commodity_costs_eur_sim_fix = information_fix_flex
            capacity_costs_eur_fix = information_fix_flex
            commodity_costs_eur_per_year_flex = information_fix_flex
            commodity_costs_eur_sim_flex = information_fix_flex
            capacity_costs_eur_flex = information_fix_flex
            if fee_type == 'SLP':
                capacity_or_basic_costs = "basic costs"

        json_results_costs = {"costs": {
            "electricity costs": {
                "per year": {
                    "total (gross)": total_costs_per_year,
                    "grid_fee": {
                        "total grid fee": round(
                            commodity_costs_eur_per_year + capacity_costs_eur, round_to_places),
                        "commodity costs": {
                            "total costs": commodity_costs_eur_per_year,
                            "costs for fixed load": commodity_costs_eur_per_year_fix,
                            "costs for flexible load": commodity_costs_eur_per_year_flex,
                        },
                        "capacity_or_basic_costs": {
                            "total costs": capacity_costs_eur,
                            "costs for fixed load": capacity_costs_eur_fix,
                            "costs for flexible load": capacity_costs_eur_flex,
                        },
                        "additional costs": round(additional_costs_per_year, round_to_places),
                    },
                    "power procurement": power_procurement_costs_per_year,
                    "levies": {
                        "EEG levy": round(eeg_costs_per_year, round_to_places),
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
                        "V2G": round(v2g_feed_in_costs_per_year, round_to_places),
                        "battery": round(battery_feed_in_costs_per_year, round_to_places)
                    },
                    "unit": "EUR",
                    "info": "energy costs for one year",
                },
                "for simulation period": {
                    "total (gross)": total_costs_sim,
                    "grid fee": {
                        "total grid fee": round(commodity_costs_eur_sim
                                                + capacity_costs_eur, round_to_places),
                        "commodity costs": {
                            "total costs": round(commodity_costs_eur_sim, round_to_places),
                            "costs for fixed load": commodity_costs_eur_sim_fix,
                            "costs for flexible load": commodity_costs_eur_sim_flex,
                        },
                        capacity_or_basic_costs: {
                            "total costs": round(capacity_costs_eur, round_to_places),
                            "costs for fixed load": capacity_costs_eur_fix,
                            "costs for flexible load": capacity_costs_eur_flex,
                        },
                        "additional costs": round(additional_costs_sim, round_to_places),
                    },
                    "power procurement": power_procurement_costs_sim,
                    "levies": {
                        "EEG levy": round(eeg_costs_sim, round_to_places),
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
                        "V2G": round(v2g_feed_in_costs_sim, round_to_places),
                        "battery": round(battery_feed_in_costs_sim, round_to_places)
                    },
                    "unit": "EUR",
                    "info": "energy costs for simulation period",
                },
            }
        }}

        # add dictionary to json with simulation data:
        with open(results_json, "r", newline="") as sj:
            simulation_json = json.load(sj)
        if strategy == "peak_load_window":  # ToDo: add flex_window?
            simulation_json["peak load time windows"].update(json_results_windows)
        simulation_json.update(json_results_costs)
        with open(results_json, "w", newline="") as sj:
            json.dump(simulation_json, sj, indent=2)

    # OUTPUT FOR SIMULATE.PY:
    return {
        "total_costs_per_year": total_costs_per_year,
        "commodity_costs_eur_per_year": commodity_costs_eur_per_year,
        "capacity_costs_eur": capacity_costs_eur,
        "power_procurement_costs_per_year": power_procurement_costs_per_year,
        "levies_fees_and_taxes_per_year": levies_fees_and_taxes_per_year,
        "feed_in_remuneration_per_year": feed_in_remuneration_per_year,
    }
