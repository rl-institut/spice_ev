from copy import deepcopy
import json
import logging
from pathlib import Path
import warnings

from spice_ev import costs

ELECTRICITY_COSTS_METHODS = ["fixed_wo_plw", "fixed_w_plw", "variable_wo_plw", "variable_w_plw"]

# entries of oemof_objective.json and their required fields ("info" is always optional)
ENTRIES = {
    "electricity_costs_method": ["value"],
    "energy_charges": ["weight"],
    "capacity_charge": ["weight"],
    "grid_connection": ["weight"],
    "feed_in_remuneration": ["weight"],
    "stationary_storage_sizing": [
        "optimize_capacity", "weight", "min_capacity_kWh", "max_capacity_kWh"],
    "stationary_storage_balanced": ["value"],
}


def get_gc_rates(price_sheet, gc_id, grid_operator, voltage_level, energy_supply_pa,
                 energy_weight):
    """ Get billing type and net energy price of a grid connector from the cost parameter file.

    RLM always uses the rates for high utilization time (>= 2500 h/a).

    :param price_sheet: content of cost parameter file (all grid operators)
    :type price_sheet: dict
    :param gc_id: grid connector ID (for messages)
    :type gc_id: str
    :param grid_operator: grid operator of grid connector (None: default_grid_operator)
    :type grid_operator: str
    :param voltage_level: voltage level of grid connector (None: MV)
    :type voltage_level: str
    :param energy_supply_pa: estimated energy supply from the grid per year in kWh
    :type energy_supply_pa: float
    :param energy_weight: weight of energy charges (0: price not needed)
    :type energy_weight: float
    :raises Exception: if a needed rate is missing in the cost parameter file
    :return: grid_operator, voltage_level, fee_type (RLM/SLP) and energy_price in €/kWh
    :rtype: dict
    """
    if grid_operator is None:
        grid_operator = "default_grid_operator"
    if voltage_level is None:
        warnings.warn(f"oemof_objective: GC {gc_id} has no voltage level, using MV")
        voltage_level = "MV"
    rates = {"grid_operator": grid_operator, "voltage_level": voltage_level,
             "fee_type": None, "energy_price": 0}
    operator_sheet = price_sheet.get(grid_operator)
    if operator_sheet is None:
        if energy_weight > 0:
            raise Exception(f"oemof_objective: GC {gc_id}: grid operator {grid_operator} "
                            f"missing in cost parameter file")
        return rates

    # billing type from annual energy, utilization time always >= 2500 h/a
    fee_type = "SLP" if energy_supply_pa <= costs.MAX_ENERGY_SUPPLY_PER_YEAR_SLP else "RLM"
    rates["fee_type"] = fee_type
    if energy_weight == 0:
        return rates
    try:
        commodity_charge = costs.find_prices(
            operator_sheet, fee_type, voltage_level,
            costs.UTILIZATION_TIME_PER_YEAR_EC, energy_supply_pa)[0]
        # net energy price in ct/kWh: no VAT
        levies = operator_sheet["levies"]
        energy_price = (
            operator_sheet["power_procurement"]["charge"]
            + commodity_charge
            + levies["EEG_levy"] + levies["chp_levy"] + levies["individual_charge_levy"]
            + levies["offshore_levy"] + levies["interruptible_loads_levy"]
            + operator_sheet["concession_fee"]["charge"]
            + operator_sheet["taxes"]["tax_on_electricity"])
    except KeyError as e:
        raise Exception(
            f"oemof_objective: GC {gc_id}: energy charges for {fee_type}, grid operator "
            f"{grid_operator}, voltage level {voltage_level}: entry {e} missing in cost "
            f"parameter file") from e
    rates["energy_price"] = energy_price / 100  # ct -> €
    return rates


def read_objective(path, gc_ids):
    """ Read and validate oemof objective file, get effective settings per grid connector.

    :param path: path to oemof_objective.json
    :type path: str or Path
    :param gc_ids: IDs of grid connectors in scenario
    :type gc_ids: iterable
    :raises Exception: if file is missing or entries are missing
    :raises ValueError: if a value is invalid
    :return: effective settings per grid connector ID
    :rtype: dict
    """
    if path is None or not Path(path).is_file():
        raise Exception(
            f"oemof_solph needs an objective file (strategy option oemof_objective), "
            f"not found: {path}")
    with open(path) as f:
        objective = json.load(f)

    # all entries except gc_overrides are required
    missing = []
    for entry, fields in ENTRIES.items():
        if entry not in objective:
            missing.append(entry)
            continue
        if not isinstance(objective[entry], dict):
            raise ValueError(f"oemof_objective {path}: {entry} must be an object")
        missing += [f"{entry}.{field}" for field in fields if field not in objective[entry]]
    if missing:
        raise Exception(
            f"oemof_objective {path}: missing entries {', '.join(missing)}")

    def check_entries(entries, location):
        # warn about unknown entries, abort on invalid values; returns known entries
        known = dict()
        for entry, fields in entries.items():
            if entry not in ENTRIES:
                warnings.warn(f"oemof_objective {location}: unknown entry {entry} ignored")
                continue
            if not isinstance(fields, dict):
                raise ValueError(f"oemof_objective {location}: {entry} must be an object")
            known[entry] = dict()
            for field, value in fields.items():
                name = f"{entry}.{field}"
                if field == "info":
                    continue
                if field not in ENTRIES[entry]:
                    warnings.warn(f"oemof_objective {location}: unknown entry {name} ignored")
                    continue
                is_number = isinstance(value, (int, float)) and not isinstance(value, bool)
                if field == "value" and entry == "electricity_costs_method":
                    valid = value in ELECTRICITY_COSTS_METHODS
                elif field in ["value", "optimize_capacity"]:
                    valid = isinstance(value, bool)
                elif field == "max_capacity_kWh":
                    valid = value is None or (is_number and value >= 0)
                else:
                    # weight, min_capacity_kWh
                    valid = is_number and value >= 0
                if not valid:
                    raise ValueError(
                        f"oemof_objective {location}: invalid value {value} for {name}")
                known[entry][field] = value
        return known

    settings = check_entries(
        {k: v for k, v in objective.items() if k != "gc_overrides"}, "top level")
    overrides = []
    for i, rule in enumerate(objective.get("gc_overrides", [])):
        location = f"gc_overrides[{i}]"
        if not isinstance(rule, dict) or not isinstance(rule.get("gcs"), list):
            raise ValueError(f"oemof_objective {location}: gcs must be a list of GC IDs")
        overrides.append((rule["gcs"], check_entries(
            {k: v for k, v in rule.items() if k != "gcs"}, location)))

    # effective settings per GC: global entries, then overrides in order (later rule wins)
    gc_settings = {gc_id: deepcopy(settings) for gc_id in gc_ids}
    for gcs, entries in overrides:
        unknown = [gc_id for gc_id in gcs if gc_id not in gc_settings]
        if unknown:
            warnings.warn(f"oemof_objective gc_overrides: GC(s) {unknown} not in scenario")
        for gc_id in gcs:
            for entry, fields in entries.items():
                gc_settings.get(gc_id, dict()).get(entry, dict()).update(fields)
    for gc_id, gc_setting in gc_settings.items():
        logging.info(f"oemof_objective: GC {gc_id} settings {gc_setting}")
    return gc_settings
