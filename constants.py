import battery
import loading_curve
import util

class Constants:
    """ constants values of a scenario
    """
    def __init__(self, obj):
        self.grid_connectors = dict({k: GridConnector(v) for k, v in obj['grid_connectors'].items()})
        self.charging_stations = dict({k: ChargingStation(v) for k, v in obj['charging_stations'].items()})
        self.vehicle_types = dict({k: VehicleType(v) for k, v in obj['vehicle_types'].items()})
        self.vehicles = dict({k: Vehicle(v, self.vehicle_types) for k, v in obj['vehicles'].items()})


class GridConnector:
    def __init__(self, obj):
        keys = [
            ('max_power', float),
        ]
        optional_keys = [
            ('current_loads', dict, {}),
            ('cost', dict, {}),
        ]
        util.set_attr_from_dict(obj, self, keys, optional_keys)

    def add_load(self, key, value):
        # add power __value__ to current_loads dict under __key__
        # return updated value
        if key in self.current_loads.keys():
            self.current_loads[key] += value
        else:
            self.current_loads[key] = value
        return self.current_loads[key]

    def get_external_load(self, exclude=[]):
        # get sum of current loads not in exclude list
        external_load = 0
        for key, value in self.current_loads.items():
            if key not in exclude:
                external_load += value
        return external_load


class ChargingStation:
    def __init__(self, obj):
        keys = [
            ('max_power', float),
            ('parent', str),
        ]
        optional_keys = [
            ('current_power', float, 0.0)
        ]
        util.set_attr_from_dict(obj, self, keys, optional_keys)


class VehicleType:
    def __init__(self, obj):
        keys = [
            ('name', str),
            ('capacity', float),
            ('charging_curve', loading_curve.LoadingCurve),
        ]
        optional_keys = [
            ('min_charging_power', float, 0.0),
        ]
        util.set_attr_from_dict(obj, self, keys, optional_keys)

        assert self.min_charging_power <= self.charging_curve.max_power


class Vehicle:
    def __init__(self, obj, vehicle_types):
        keys = [
            ('vehicle_type', vehicle_types.get),
        ]
        optional_keys = [
            ('connected_charging_station', str, None),
            ('estimated_time_of_arrival', util.datetime_from_isoformat, None),
            ('estimated_time_of_departure', util.datetime_from_isoformat, None),
            ('desired_soc', float, 100.),
            ('soc', float, 0.),
        ]
        util.set_attr_from_dict(obj, self, keys, optional_keys)

        # Add battery object to vehicles
        self.battery = battery.Battery(
            self.vehicle_type.capacity,
            self.vehicle_type.charging_curve,
            self.soc,
        )
        del self.soc

    def get_delta_soc(self):
        return self.desired_soc - self.battery.soc
