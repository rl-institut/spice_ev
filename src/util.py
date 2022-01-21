import datetime
import json
from math import sqrt
from sys import version_info


def datetime_from_isoformat(s):
    """Converts isoformat str to datetime.

    :param s: date in isoformat
    :type s: str
    :return: datetime
    :rtype: datetime
    """
    if s is None:
        return None

    if (version_info.major, version_info.minor) >= (3, 7):
        # fromisoformat introduced in Python 3.7
        return datetime.datetime.fromisoformat(s)

    # fallback: use strptime. Problem is timezone with colon
    # Thanks SO! (https://stackoverflow.com/questions/30999230/how-to-parse-timezone-with-colon)
    if s[-3:-2] == ':':
        s = s[:-3]+s[-2:]
    return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z")


def datetime_within_window(dt, time_windows):
    """Checks if a given datetime is within any of the given time windows.

    :param dt: time
    :type dt: datetime
    :param time_windows: Structure of time_windows: {season: {"start": datetime with start date and\
        start time, "end": datetime with end date end end time}}
    :type time_windows: dict

    note:
    The times pertain to all dates within the window.
    E.g., 14.03. 9:00 - 14.05. 11:00 means 09:00 - 11:00 for all days in two months
    14.04. 10:00 is within this eaxmple window, 14.04. 12:00 is not
    """
    for window in time_windows.values():
        start = window["start"].replace(year=dt.year)
        end = window["end"].replace(year=dt.year)
        if start.date() <= dt.date() <= end.date():
            # this season
            return start.time() <= dt.time() < end.time()
    return False


def dt_within_core_standing_time(dt, core_standing_time):
    """
    Checks if datetime dt is in inside core standing time.


    :param dt: time to be checked
    :type dt: datetime
    :param core_standing_time: Provides time_windows to check
            Example: one core standing time each day from 22:00 to 5:00 next day
            additionally weekends:
            {"times": [{"start": (22,0), "end":(5,0)}], "full_days": [6,7]}
    :type core_standing_time: dict
    :return: True - if dt is inside a time_window or if core_standing_time=None.
        False - if dt is outside of time window
    :rtype: bool
    """

    if core_standing_time is None:
        return True

    if any([day_off == dt.isoweekday()
            for day_off in core_standing_time.get('full_days', [])]):
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
    """
    Returns cost based on the cost type.

    :param x: coefficient
    :type x: numeric
    :param cost_dict: dictionary with costs
    :type cost_dict: dict
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
    """
    Returns power for a given price.

    :param y:
    :type y: numeric
    :param cost_dict: dictionary with costs
    :type cost_dict: dict
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
    """
    Returns power that is actually available at charging station.

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


def set_options_from_config(args, check=False, verbose=True):
    """Read options from config file, update given args, try to parse options
    , ignore comment lines (begin with #)
    :param args: input arguments
    :type args: argparse.Namespace
    :param check: raise ValueError on unknown options
    :type check: bool
    :param verbose: gives final overview of arguments
    :type bool
    """

    if "config" in args and args.config is not None:
        # read options from config file
        with open(args.config, 'r') as f:
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
                # known option?
                if (k not in args) and check:
                    raise ValueError("Unknown option {}".format(k))
                # set option
                vars(args)[k] = v
        # Give overview of options
        if verbose:
            print("Options: {}".format(vars(args)))
