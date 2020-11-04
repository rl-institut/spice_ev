import datetime
from math import sqrt

def datetime_from_isoformat(s):
    if s is None:
        return None

    # Thanks SO! (https://stackoverflow.com/questions/30999230/how-to-parse-timezone-with-colon)
    if ":" == s[-3:-2]:
        s = s[:-3]+s[-2:]
    return datetime.datetime.strptime(s, '%Y-%m-%dT%H:%M:%S%z')


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
        # cost = 0
        # for power, coeff in enumerate(cost_dict["value"]):
            # cost += coeff * pow(x, power)
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
        while cost_dict["value"][-1] == 0:
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
