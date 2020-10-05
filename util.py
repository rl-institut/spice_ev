import datetime


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
        cost = cost_dict["value"].pop(0) # base coefficient, independent of x
        for coeff in cost_dict["value"]:
            cost += coeff * x
            x *= x
        return cost
    else:
        raise NotImplementedError
