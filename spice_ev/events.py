import csv
import datetime
from pathlib import Path
from warnings import warn

from spice_ev import util


class Events:
    """ Events class

        Sets up events:
        * external_load
        * energy_feed_in
        * grid_operator_signals - price
        * grid_operator_signals - schedule
        * vehicle_events
    """
    def __init__(self, obj, dir_path):
        dir_path = Path(dir_path)
        # optional
        self.external_load_lists = dict(
            {k: EnergyValuesList(v, dir_path) for k, v in obj.get('external_load', {}).items()})
        self.energy_feed_in_lists = dict(
            {k: EnergyValuesList(v, dir_path) for k, v in obj.get('energy_feed_in', {}).items()})
        self.grid_operator_signals = list(
            [GridOperatorSignal(x) for x in obj.get('grid_operator_signals', [])])
        self.grid_operator_signals += get_energy_price_list_from_csv(
            obj.get('energy_price_from_csv', None), dir_path)
        self.grid_operator_signals += get_schedule_from_csv(
            obj.get('schedule_from_csv', None), dir_path)
        self.vehicle_events = list(
            [VehicleEvent(x) for x in obj.get('vehicle_events')])

    def get_event_steps(self, start_time, n_intervals, interval):
        """
        Creates list of all events within simulation time.

        :param start_time: starting time of the simulation
        :type start_time: datetime
        :param n_intervals: total number of intervals
        :type n_intervals: int
        :param interval: length of one interval
        :type interval: timestemp
        :return: list of all events
        :rtype: list
        """
        steps = list([[] for _ in range(n_intervals)])

        all_events = self.vehicle_events + self.grid_operator_signals
        for name, load_list in self.external_load_lists.items():
            all_events.extend(load_list.get_events(name, ExternalLoad))
        for name, feed_in_list in self.energy_feed_in_lists.items():
            all_events.extend(
                feed_in_list.get_events(name, EnergyFeedIn, has_perfect_foresight=True))

        ignored = 0

        for event in all_events:
            # get ceil of time index (start of next interval)
            index = -((start_time - event.signal_time) // interval)

            if index < 0:
                warn('Event is before start of scenario, placing at first time step: ' + str(event))
                steps[0].append(event)
            elif index >= n_intervals:
                ignored += 1
            else:
                steps[index].append(event)

        if ignored:
            warn('{} events ignored after end of scenario'.format(ignored))

        return steps


class Event:
    """ Event class"""
    def __str__(self):
        return '{}, {}'.format(self.__class__.__name__, vars(self))


class EnergyFeedIn(Event):
    """EnergyFeedIn class"""
    def __init__(self, kwargs):
        self.__dict__.update(**kwargs)


class ExternalLoad(Event):
    """ExternalLoad class"""
    def __init__(self, kwargs):
        self.__dict__.update(**kwargs)


class EnergyValuesList:
    """EnergyValuesList class"""
    def __init__(self, obj, dir_path):
        keys = [
            ('start_time', util.datetime_from_isoformat),
            ('step_duration_s', float),
            ('grid_connector_id', str),
        ]
        optional_keys = [
            ('values', lambda x: list(map(float, x)), []),
            ('factor', float, 1),
        ]
        util.set_attr_from_dict(obj, self, keys, optional_keys)

        if 'values' in obj and 'csv_file' in obj:
            raise Exception("Either values or csv_file, not both!")

        # Read CSV file if values are not given directly
        if not self.values:
            csv_path = dir_path / obj['csv_file']
            column = obj['column']

            with open(csv_path, newline='') as csvfile:
                reader = csv.DictReader(csvfile, delimiter=',', quotechar='"')
                for row in reader:
                    self.values.append(float(row[column]))

    def get_events(self, name, value_class, has_perfect_foresight=False):
        """
        Sets up feed_in and external_load events from input.

        :param name: name of the input csv file
        :type name: str
        :param value_class: object (e.g. EnergyFeedIn)
        :type value_class: object
        :param has_perfect_foresight: true if system knows about future events
        :type has_perfect_foresight: bool
        :return: list of events
        :rtype: list
        """
        assert value_class in [EnergyFeedIn, ExternalLoad]

        eventlist = []
        time_delta = datetime.timedelta(seconds=self.step_duration_s)
        for idx, value in enumerate(self.values):
            idx_time = self.start_time + time_delta * idx
            eventlist.append(value_class({
                "signal_time": self.start_time if has_perfect_foresight else idx_time,
                "start_time": idx_time,
                "name": name,
                "grid_connector_id": self.grid_connector_id,
                "value": value * self.factor,
            }))

        return eventlist


class GridOperatorSignal(Event):
    """GridOperatorSignal class"""
    def __init__(self, obj):
        keys = [
            ('signal_time', util.datetime_from_isoformat),
            ('start_time', util.datetime_from_isoformat),
            ('grid_connector_id', str),
        ]
        optional_keys = [
            ('max_power', float, None),
            ('cost', dict, None),
            ('target', float, None),
            ('window', bool, None)
        ]
        util.set_attr_from_dict(obj, self, keys, optional_keys)


def get_energy_price_list_from_csv(obj, dir_path):
    """
    Get energy price list from input csv.

    :param obj: dictionary with information about input csv
    :type obj: dict
    :param dir_path: directory
    :type dir_path: Path

    :return: grid operator signal events
        list
    """
    if not obj:
        return []
    start = util.datetime_from_isoformat(obj["start_time"])
    events = []
    interval = datetime.timedelta(seconds=obj["step_duration_s"])
    yesterday = datetime.timedelta(days=1)

    csv_path = dir_path / obj['csv_file']
    column = obj['column']

    with open(csv_path, newline='') as csvfile:
        reader = csv.DictReader(csvfile, delimiter=',', quotechar='"')
        for idx, row in enumerate(reader):
            start_time = idx * interval + start
            event_time = max(start, start_time-yesterday)
            events.append(GridOperatorSignal({
                "start_time": start_time.isoformat(),
                "signal_time": event_time.isoformat(),
                "grid_connector_id": obj["grid_connector_id"],
                "cost": {"type": "fixed", "value": float(row[column])}
            }))
    return events


def get_schedule_from_csv(obj, dir_path):
    """
    Read out schedule CSV file, generate list of GridOperatorSignal events
    Only changed target values generate a new event
    Ignore any timestamp in file, assume constant stride

    :param obj: dictionary with information about input csv
    :type obj: dict
    :param dir_path: directory
    :type dir_path: Path
    :raises SystemExit: if specified schedule *column* is not present in input file
    :return: grid operator schedule
    :rtype: list
    """

    # no CSV file/no info: skip
    if not obj:
        return []

    schedule = []
    col = obj['column']
    window_col = obj.get('window_column', 'charge')

    # fallback if timesteps can't be parsed
    start = util.datetime_from_isoformat(obj.get("start_time", None))
    interval = datetime.timedelta(seconds=obj.get("step_duration_s", None))

    # remember last target value
    last_target = None
    last_window = None

    csv_path = dir_path / obj['csv_file']
    with open(csv_path, newline='') as csvfile:
        # reader = csv.DictReader(csvfile, delimiter=',', quotechar='"')
        # assert col in reader.fieldnames, "'{}' is not a column of {}".format(col, obj['csv_file'])
        reader = csv.reader(csvfile, delimiter=',', quotechar='"')
        header = next(reader)
        header = list(map(lambda x: x.strip(), header))
        try:
            col_idx = header.index(col)
        except ValueError:
            raise SystemExit("'{}' is not a column of {}".format(col, obj['csv_file']))

        window_col_idx = header.index(window_col) if window_col in header else None

        if obj.get('individual'):
            vehicle_names = header[3:]
            vehicle_schedules = [None]*len(vehicle_names)
        else:
            vehicle_names = []

        for idx, row in enumerate(reader):
            # only generate events for changed schedule, so compare target values
            target = float(row[col_idx])
            window = row[window_col_idx].strip() == '1' if window_col_idx is not None else None

            if target != last_target or window != last_window:
                # targets/window different: generate new event
                last_target = target
                last_window = window

                # get start_time
                try:
                    # read out event start time from first column
                    start_time = util.datetime_from_isoformat(row[0])
                    if (start is None or start.tzinfo) and not start_time.tzinfo:
                        # make timezone-aware for comparison
                        start_time = start_time.replace(
                            tzinfo=datetime.timezone(datetime.timedelta(hours=2)))
                    # default for start: use first start_time
                    start = start or start_time
                except ValueError:
                    # could not parse time: get start time from position in file
                    start_time = idx * interval + start

                # convention: schedule sent one day before at 9am, valid from noon
                if start_time.hour < 12:
                    signal_time = start_time - datetime.timedelta(days=2)
                else:
                    signal_time = start_time - datetime.timedelta(days=1)
                signal_time = signal_time.replace(hour=9, minute=0, second=0)
                # don't signal before start of simulation
                signal_time = max(start, signal_time)

                assert signal_time <= start_time, (
                    "Wrong signal in {} at index {}, starts before being sent (check your dates!)"
                    .format(obj['csv_file'], idx + 1))

                schedule.append(GridOperatorSignal({
                    "start_time": start_time.isoformat(),
                    "signal_time": signal_time.isoformat(),
                    "grid_connector_id": obj["grid_connector_id"],
                    "target": target,
                    "window": window,
                }))

            for i, vid in enumerate(vehicle_names):
                v_schedule = float(row[i+3])
                if v_schedule != vehicle_schedules[i]:
                    schedule.append(VehicleEvent({
                        "start_time": start_time.isoformat(),
                        "signal_time": signal_time.isoformat(),
                        "vehicle_id": vid,
                        "event_type": "schedule",
                        "update": {"schedule": v_schedule},
                    }))
                    vehicle_schedules[i] = v_schedule

    return schedule


class VehicleEvent(Event):
    """VehicleEvent class"""
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
            ('soc_delta', float),
            ('desired_soc', float),
            ('schedule', float),
        ]

        for name, func in conversions:
            if name in self.update:
                self.update[name] = func(self.update[name])
