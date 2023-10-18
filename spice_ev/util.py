import csv
import datetime
import json
from math import sqrt
import warnings


def datetime_from_isoformat(s):
    """Convert isoformat str to datetime.

    :param s: date in isoformat
    :type s: str
    :return: datetime or None if input is None
    :rtype: datetime
    """

    if s is None:
        return None
    return datetime.datetime.fromisoformat(s)


def datetime_within_time_window(dt, time_windows, voltage_level):
    """Check if a given datetime is within time window of a certain voltage level.

    structure: season -> start (date), end (date), windows -> voltage level -> [[start, end], ...]

    :param dt: time
    :type dt: datetime
    :param time_windows: time windows to check
    :type time_windows: dict
    :param voltage_level: voltage level to check
    :type voltage_level: string
    :return: is datetime within time window?
    :rtype: bool
    """
    for season in time_windows.values():
        if season["start"] <= dt.date() <= season["end"]:
            # same season: check times of voltage level
            windows = season.get("windows", {}).get(voltage_level, [])
            for window in windows:
                if window[1] < window[0]:
                    # crossing midnight
                    if dt.time() >= window[0] or dt.time() < window[1]:
                        return True
                elif window[0] <= dt.time() < window[1]:
                    # within interval
                    return True
            # same season, but not within time windows: skip other seasons
            return False
    return False


def dt_within_core_standing_time(dt, core_standing_time):
    """ Check if datetime dt is in inside core standing time.

    :param dt: time to be checked
    :type dt: datetime
    :param core_standing_time: Provides time_windows to check
            Example: one core standing time each day from 22:00 to 5:00 next day
            additionally weekends:
            {"times": [{"start": (22,0), "end":(5,0)}], "no_drive_days": [5,6]}
    :type core_standing_time: dict
    :return: True - if dt is inside a time_window or if core_standing_time=None.
        False - if dt is outside of time window
    :rtype: bool
    """

    if core_standing_time is None:
        return True

    if any([day_off == dt.weekday()
            for day_off in core_standing_time.get('no_drive_days', [])]):
        return True

    if dt.date().isoformat() in core_standing_time.get('holidays', []):
        return True

    current_time = dt.time()
    for time_window in core_standing_time.get('times', []):
        core_standing_time_start, core_standing_time_end = [
            datetime.time(*time_window[key]) for key in ['start', 'end']
        ]
        # distinct handling necessary depending on whether standing time over midnight or not
        if core_standing_time_end < core_standing_time_start:
            if (current_time >= core_standing_time_start or current_time < core_standing_time_end):
                return True
        else:
            if core_standing_time_start <= current_time <= core_standing_time_end:
                return True

    return False


def set_attr_from_dict(source, target, keys, optional_keys):
    """ Set attributes of `target` from a `source` dictionary.

    None values for optional keys are not converted.

    :param source: dictionary
    :type source: dict
    :param target: target object
    :type target: object
    :param keys: parameter list
    :type keys: list
    :param optional_keys: parameter list
    :type optional_keys: list
    """

    for n, conversion in keys:
        setattr(target, n, conversion(source[n]))
    for n, conversion, default in optional_keys:
        if n not in source or source[n] is None:
            setattr(target, n, default)
        else:
            setattr(target, n, conversion(source[n]))


def get_cost(x, cost_dict):
    """ Return cost based on the cost type.

    :param x: coefficient
    :type x: numeric
    :param cost_dict: dictionary with costs
    :type cost_dict: dict
    :raises NotImplementedError: if costs are neither fixed nor polynomial
    :return: cost
    :rtype: numeric
    """

    if cost_dict["type"] == "fixed":
        return cost_dict["value"] * x
    elif cost_dict["type"] == "polynomial":
        base = 1
        cost = 0
        for coeff in cost_dict["value"]:
            cost += coeff * base
            base *= x
        return cost
    else:
        raise NotImplementedError


def get_power(y, cost_dict):
    """ Return power for a given price.

    :param y: price
    :type y: numeric
    :param cost_dict: dictionary with costs
    :type cost_dict: dict
    :raises NotImplementedError: if costs are neither fixed nor polynomial
    :return: power
    :rtype: numeric
    """

    # how much power for a given price?
    if y is None:
        return None
    if cost_dict["type"] == "fixed":
        return y / cost_dict["value"]
    elif cost_dict["type"] == "polynomial":
        while len(cost_dict["value"]) > 0 and cost_dict["value"][-1] == 0:
            # reduce cost polynom until highest coefficient != 0
            cost_dict["value"].pop()
        if len(cost_dict["value"]) <= 1:
            # fixed cost: question makes no sense
            return None
        elif len(cost_dict["value"]) == 2:
            # linear
            (a0, a1) = cost_dict["value"]
            return (y - a0) / a1
        elif len(cost_dict["value"]) == 3:
            (a0, a1, a2) = cost_dict["value"]
            p = a1/a2
            q = (a0 - y) / a2
            # single solution: higher value
            return -p/2 + sqrt(p*p/4 - q)
            # x1 = -p/2 - sqrt(p*p/4 - q)
            # x2 = -p/2 + sqrt(p*p/4 - q)
            # y1 = get_cost(x1, cost_dict)
            # return x1 if y1 == y else x2

    raise NotImplementedError


def clamp_power(power, vehicle, cs):
    """ Return power that is actually available at charging station.

    :param power: available charging power
    :type power: numeric
    :param vehicle: Vehicle object
    :type vehicle: vehicle object
    :param cs: Charging station object
    :type cs: object
    :return: power
    :rtype: numeric
    """

    # how much of power can vehicle at cs actually use
    total_power = min(cs.current_power + power, cs.max_power)
    if total_power < cs.min_power or total_power < vehicle.vehicle_type.min_charging_power:
        power = 0
    else:
        # cs.current_power can be larger than max_power by x<EPS. Avoid power<0
        power = max(min(power, cs.max_power - cs.current_power), 0)
    return power


def set_options_from_config(args, check=None, verbose=True):
    """ Update given options from config file.

    Read config file, try to parse options, ignore comment lines (begin with #).

    :param args: input arguments
    :type args: argparse.Namespace
    :param check: check config options against argparser
    :type check: argparse.ArgumentParser
    :param verbose: gives final overview of arguments
    :type verbose: bool

    :raise argparse.ArgumentError: Raised if wrong option values are given
    :raises Exception: Raised if unknown option is given or value could not be converted
    """

    if "config" in args and args.config is not None:
        # read options from config file
        with open(args.config, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith('#'):
                    # comment
                    continue
                if len(line) == 0:
                    # empty line
                    continue
                k, v = line.split('=')
                k = k.strip()
                v = v.strip()
                try:
                    # option may be special: number, array, etc.
                    v = json.loads(v)
                except ValueError:
                    # or not
                    pass
                # check option
                if check is not None:
                    # find action by name
                    try:
                        action = [a for a in check._actions if a.dest == k][0]
                    except IndexError:
                        raise Exception(f"Unknown option {k}")
                    # check each item in list individually
                    v_list = [v] if type(v) is not list else v
                    for v_item in v_list:
                        # check item. Returns None on success
                        # may raise ArgumentError if not successful
                        try:
                            if action.type is not None:
                                v_item = action.type(v_item)
                            check._check_value(action, v_item)
                        except Exception:
                            print(f"Failed check {k}: {v}")
                            raise
                    else:
                        # all checks successful: set argument
                        vars(args)[k] = v
                else:
                    # set option
                    vars(args)[k] = v

        # Give overview of options
        if verbose:
            print("Options: {}".format(vars(args)))


def sanitize(s, chars=''):
    """ Remove special characters from string.

    Used to make strings safe for file paths.

    :param s: input to be sanitized
    :type s: string
    :param chars: characters to replace
    :type chars: string
    :return: input without special characters in chars
    :rtype: string
    """

    if not chars:
        chars = '</|\\>:"?*'
    return s.translate({ord(c): "" for c in chars})


def read_grid_file(grid_path):
    """ Read in grid file.

    | Should be CSV with columns "residual load" and curtailment.
    | Optional column "timestamp" in ISO format.

    :param grid_path: path to grid situation file
    :type: grid_path: str
    :return: residual_load, curtailment and grid_start_time (if timestamps are given, else None)
    :rtype: triple
    """

    residual_load = []
    curtailment = []
    curtailment_is_positive = False
    curtailment_is_negative = False
    # Read grid situation timeseries
    with open(grid_path, 'r', newline='') as f:
        reader = csv.DictReader(f)
        for row_idx, row in enumerate(reader):
            # get start time of grid situation series
            if row_idx == 0:
                try:
                    grid_start_time = datetime.datetime.strptime(row["timestamp"], "%Y-%m-%d %H:%M")
                except (ValueError, KeyError):
                    warnings.warn('Time component of grid situation timeseries ignored. '
                                  'Must be of format YYYY.MM.DD HH:MM')
                    grid_start_time = None

            # store residual_load value, use previous value if none provided
            try:
                residual_load.append(float(row["residual load"]))
            except ValueError:
                warnings.warn("Residual load timeseries contains non-numeric values.")
                replace_unknown = residual_load[-1] if row_idx > 0 else 0
                residual_load.append(replace_unknown)
            # store curtailment info
            try:
                # sign of curtailment not clear
                curtailment_value = float(row["curtailment"])
                # at least make sure it is consistent
                curtailment_is_negative |= curtailment_value < 0
                curtailment_is_positive |= curtailment_value > 0
                assert not (curtailment_is_negative and curtailment_is_positive)
                curtailment.append(abs(curtailment_value))
            except ValueError:
                warnings.warn("Curtailment timeseries contains non-numeric values.")
                replace_unknown = curtailment[-1] if row_idx > 0 else 0
                curtailment.append(replace_unknown)

    assert len(residual_load) == len(curtailment)
    return residual_load, curtailment, grid_start_time
