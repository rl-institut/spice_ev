import datetime
import json
from math import sqrt
from sys import version_info
from typing import Type


def datetime_from_isoformat(s):
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
    """
    Checks if a given datetime is within any of the given time windows.

    Structure of time_windows: {season: {
        "start": datetime with start date and start time,
        "end": datetime with end date end end time}}
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


def timestep_within_window(time_windows, current_datetime=None, timestep=None, start_time=None, interval=None):
    """
    Checks if timestep is core standing times.

    Args:
        timestep: Time step in simulation (int)
        time: Current time (datetime.time obj)
        start_time: Start time of the simulation (datetime.datetime)
        interval: Size of time steps for simulation (datetime.timedelta obj)
        time_windows: Provides time_windows to check 
            e.g. {time_windows:[{'start': (22,0), 'end':(5,0)}]
                full_days: [6,7]}      
    """

    if time_windows is None:
        return True

    if current_datetime is None:
        try:
            current_datetime = start_time + timestep * interval
        except TypeError: 
            raise ValueError("Either current_datetime or timestep, start_time and interval must be provided.")
    
    if any([day_off == current_datetime.isoweekday() 
            for day_off in time_windows.get('full_days', [])]):
        return True
    
    current_time = current_datetime.time()
    for time_window in time_windows['times']:
        core_standing_time_start, core_standing_time_end = [
            datetime.time(*time_window[key]) for key in ['start', 'end']
            ]

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
    """
    for n, conversion in keys:
        setattr(target, n, conversion(source[n]))
    for n, conversion, default in optional_keys:
        if n not in source or source[n] is None:
            setattr(target, n, default)
        else:
            setattr(target, n, conversion(source[n]))


def get_cost(x, cost_dict):
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
    power = min(power, cs.max_power - cs.current_power)
    if power < cs.min_power or power < vehicle.vehicle_type.min_charging_power:
        power = 0
    return power


def set_options_from_config(args, check=False, verbose=True):
    # read options from config file, update given args
    # try to parse options, ignore comment lines (begin with #)
    # check: raise ValueError on unknown options
    # verbose: gives final overview of arguments
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
