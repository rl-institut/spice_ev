import csv
import os
import util


class Events:
    """ events
    """
    def __init__(self, obj, dir_path, constants):
        # optional
        self.external_loads = dict({k: ExternalLoad(v, dir_path) for k, v in obj.get('external_load', {}).items()})
        self.grid_operator_signals = list([GridOperatorSignal(x, constants) for x in obj.get('grid_operator_signals')])
        self.vehicle_events = list([VehicleEvent(x, constants) for x in obj.get('vehicle_events')])


class ExternalLoad:
    def __init__(self, obj, dir_path):
        keys = [
            ('start_time', util.datetime_from_isoformat),
            ('step_duration_s', float),
        ]
        optional_keys = [
            ('values', lambda x: list(map(float, x)), []),
        ]
        util.set_attr_from_dict(obj, self, keys, optional_keys)

        if 'values' in obj and 'csv_file' in obj:
            raise Exception("Either values or csv_file, not both!")

        # Read CSV file of values are not given directly
        if not self.values:
            csv_path = os.path.join(dir_path, obj['csv_file'])
            column = obj['column']

            with open(csv_path, newline='') as csvfile:
                reader = csv.DictReader(csvfile, delimiter=',', quotechar='"')
                for row in reader:
                    self.values.append(float(row[column]))

        print(self.__class__.__name__, vars(self))


class GridOperatorSignal:
    def __init__(self, obj, constants):
        keys = [
            ('signal_time', util.datetime_from_isoformat),
            ('start_time', util.datetime_from_isoformat),
            ('stop_time', util.datetime_from_isoformat),
            ('grid_connector_id', constants.grid_connectors.get),
            ('cost', dict),
        ]
        optional_keys = [
            ('max_power', float, None),
        ]
        util.set_attr_from_dict(obj, self, keys, optional_keys)
        print(self.__class__.__name__, vars(self))


class VehicleEvent:
    def __init__(self, obj, constants):
        keys = [
            ('signal_time', util.datetime_from_isoformat),
            ('start_time', util.datetime_from_isoformat),
            ('vehicle_id', constants.vehicles.get),
            ('event_type', str),
            ('update', dict),
        ]
        optional_keys = [
        ]
        util.set_attr_from_dict(obj, self, keys, optional_keys)

        # convert types of `update` member
        conversions = [
            ('estimated_time_of_arrival', util.datetime_from_isoformat),
            ('estimated_time_of_departure', util.datetime_from_isoformat),
            ('connected_charging_station', constants.charging_stations.get),
            ('energy_delta', float),
            ('desired_soc', float),
        ]

        for name, func in conversions:
            if name in self.update:
                self.update[name] = func(self.update[name])

        print(self.__class__.__name__, vars(self))
