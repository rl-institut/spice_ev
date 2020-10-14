import csv
from math import ceil
import os
import datetime
import util


class Events:
    """ events
    """
    def __init__(self, obj, dir_path):
        # optional
        self.external_load_lists = dict({k: ExternalLoadList(v, dir_path) for k, v in obj.get('external_load', {}).items()})
        self.grid_operator_signals = list([GridOperatorSignal(x) for x in obj.get('grid_operator_signals')])
        self.vehicle_events = list([VehicleEvent(x) for x in obj.get('vehicle_events')])


    def get_event_steps(self, start_time, n_intervals, interval):
        steps = list([[] for _ in range(n_intervals)])

        current_time = start_time

        all_events = self.vehicle_events + self.grid_operator_signals
        for name, load_list in self.external_load_lists.items():
            all_events.extend(load_list.get_events(name))

        ignored = 0

        for event in all_events:
            index = ceil((event.signal_time - start_time) / interval)

            if index < 0:
                print('Warning: Event is before start of scenario, placing at first time step:', event)
                steps[0].append(event)
            elif index >= n_intervals:
                ignored += 1
            else:
                steps[index].append(event)

        if ignored:
            print('Warning: {} events ignored after end of scenario'.format(ignored))

        return steps


class Event:
    def __str__(self):
        return '{}, {}'.format(self.__class__.__name__, vars(self))


class ExternalLoad(Event):
    def __init__(self, kwargs):
        self.__dict__.update(**kwargs)


class ExternalLoadList:
    def __init__(self, obj, dir_path):
        keys = [
            ('start_time', util.datetime_from_isoformat),
            ('step_duration_s', float),
            ('grid_connector_id', str),
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

    def get_events(self, name):
        eventlist = []
        time_delta = datetime.timedelta(seconds=self.step_duration_s)
        for idx, value in enumerate(self.values):
            idx_time = self.start_time + time_delta * idx
            eventlist.append(ExternalLoad({
                "signal_time": idx_time,
                "start_time": idx_time,
                "name": name,
                "grid_connector_id": self.grid_connector_id,
                "value": value,
            }))

        return eventlist


class GridOperatorSignal(Event):
    def __init__(self, obj):
        keys = [
            ('signal_time', util.datetime_from_isoformat),
            ('start_time', util.datetime_from_isoformat),
            ('grid_connector_id', str),
        ]
        optional_keys = [
            ('max_power', float, None),
            ('cost', dict, None),
        ]
        util.set_attr_from_dict(obj, self, keys, optional_keys)


class VehicleEvent(Event):
    def __init__(self, obj):
        keys = [
            ('signal_time', util.datetime_from_isoformat),
            ('start_time', util.datetime_from_isoformat),
            ('vehicle_id', str),
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
            ('connected_charging_station', str),
            ('soc_delta', float),
            ('desired_soc', float),
        ]

        for name, func in conversions:
            if name in self.update:
                self.update[name] = func(self.update[name])
