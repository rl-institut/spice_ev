import util

class Constants:
    """ constants values of a scenario
    """
    def __init__(self, obj):
        self.grid_connectors = dict({k: GridConnector(v) for k, v in obj['grid_connectors'].items()})
        self.charging_stations = dict({k: ChargingStation(v) for k, v in obj['charging_stations'].items()})
        self.vehicle_types = dict({k: VehicleType(v) for k, v in obj['vehicle_types'].items()})
        self.vehicles = dict({k: Vehicle(v, self) for k, v in obj['vehicles'].items()})


class GridConnector:
    def __init__(self, obj):
        keys = [
            ('max_power', float),
        ]
        optional_keys = [
            ('current_loads', dict, {}),
            ('cost', dict, {}),
            ('cur_max_power', float, None),
        ]
        util.set_attr_from_dict(obj, self, keys, optional_keys)
        print(self.__class__.__name__, vars(self))


class ChargingStation:
    def __init__(self, obj):
        keys = [
            ('max_power', float),
        ]
        optional_keys = [
            ('current_power', float, 0.0)
        ]
        util.set_attr_from_dict(obj, self, keys, optional_keys)
        print(self.__class__.__name__, vars(self))


class VehicleType:
    def __init__(self, obj):
        keys = [
            ('name', str),
            ('capacity', float),
            ('max_charging_power', float),
        ]
        optional_keys = [
        ]
        util.set_attr_from_dict(obj, self, keys, optional_keys)
        print(self.__class__.__name__, vars(self))

        #TODO charging_curve


class Vehicle:
    def __init__(self, obj, constants):
        keys = [
            ('vehicle_type', constants.vehicle_types.get),
        ]
        optional_keys = [
            ('connected_charging_station', constants.charging_stations.get, None),
            ('estimated_time_of_arrival', util.datetime_from_isoformat, None),
            ('estimated_time_of_departure', util.datetime_from_isoformat, None),
            ('desired_soc', float, 100.),
            ('soc', float, 0.),
            ("energy_delta", float, 0.0),
        ]
        util.set_attr_from_dict(obj, self, keys, optional_keys)
        print(self.__class__.__name__, vars(self))
