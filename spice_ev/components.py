import datetime
from spice_ev import battery, loading_curve, util
from spice_ev.events import FixedLoad


class Components:
    """ Components class

    Collection of all components:

    * grid_connectors
    * charging_stations
    * vehicle_types
    * vehicles
    * batteries
    * photovoltaics
    """

    def __init__(self, obj):
        self.grid_connectors = dict(
            {k: GridConnector(v) for k, v in obj.get('grid_connectors', {}).items()})
        self.charging_stations = dict(
            {k: ChargingStation(v) for k, v in obj.get('charging_stations', {}).items()})
        self.vehicle_types = dict(
            {k: VehicleType(v) for k, v in obj.get('vehicle_types', {}).items()})
        self.vehicles = dict(
            {k: Vehicle(v, self.vehicle_types) for k, v in obj.get('vehicles', {}).items()})
        self.batteries = dict(
            {k: StationaryBattery(v) for k, v in obj.get('batteries', {}).items()})
        self.photovoltaics = dict(
            {k: Photovoltaics(v) for k, v in obj.get('photovoltaics', {}).items()})


class GridConnector:
    """GridConnector class"""
    def __init__(self, obj):
        keys = [
            ('max_power', float),
        ]
        optional_keys = [
            ('current_loads', dict, {}),
            ('number_cs', int, None),
            ('cost', dict, {}),
            ('target', float, None),
            ('window', bool, None),
            ('voltage_level', str, None),
            ('grid_operator', str, None),
        ]
        util.set_attr_from_dict(obj, self, keys, optional_keys)
        self.avg_fixed_load = None
        self.cur_max_power = self.max_power

    def add_load(self, key, value):
        """ Add power *value* to current_loads dict under *key*, return updated value.

        :param key: key of dictionary
        :type key: str
        :param value: value to be added
        :type value: numeric
        :return: updated dict
        :rtype: dict
        """

        if key in self.current_loads.keys():
            self.current_loads[key] += value
        else:
            self.current_loads[key] = value
        return self.current_loads[key]

    def get_current_load(self, exclude=[]):
        """ Get sum of current loads not in *exclude* list.

        :param exclude: list of keys that should be excluded
        :type exclude: list
        :return: current load
        :rtype: numeric
        """

        current_load = 0
        for key, value in self.current_loads.items():
            if key not in exclude:
                current_load += value
        return current_load

    def add_avg_fixed_load_week(self, fixed_load_list, interval):
        """ Compute average load using EnergyValuesList.

        Each weekday has its own sequence of average values, depending on interval.
        Multiple fixed loads are added up.

        :param fixed_load_list: list of fixed loads
        :type fixed_load_list: list
        :param interval: interval of one timestep
        :type interval: timedelta
        """

        # convert EnergyValuesList to event list
        events = fixed_load_list.get_events(None, FixedLoad, has_perfect_foresight=False)
        events_per_day = int(datetime.timedelta(hours=24) / interval)
        values_by_weekday = [[[] for _ in range(events_per_day)] for _ in range(7)]

        # iterate over event list, to find which fixed load is present during which interval step
        # take care when EnergyValuesList.step_duration_s != interval (not in sync)
        # last event in interval used, similar to strategy implementation
        cur_time = fixed_load_list.start_time - interval
        cur_value = None
        while True:
            cur_time += interval

            if len(events) == 0:
                break

            # get last event for this timestep
            while len(events) > 0 and events[0].start_time <= cur_time:
                event = events.pop(0)
                cur_value = event.value

            # insert fixed load value into specific timeslot
            if cur_value is not None:
                weekday = cur_time.weekday()
                midnight = cur_time.replace(hour=0, minute=0)
                timeslot = int((cur_time - midnight) / interval)
                values_by_weekday[weekday][timeslot].append(cur_value)

        # compute averages
        avg_values_by_weekday = [[
            (sum(v) / len(v)) if len(v) > 0 else 0 for v in day_values
        ] for day_values in values_by_weekday]

        # set/update avg_fixed_load for this GC
        if self.avg_fixed_load is None:
            self.avg_fixed_load = avg_values_by_weekday
        else:
            # multiple fixed loads: add up
            for i, values in enumerate(avg_values_by_weekday):
                self.avg_fixed_load[i] = [e + v for (e, v) in zip(self.avg_ficed_load[i], values)]

    def get_avg_fixed_load(self, dt, interval):
        """ Get average fixed load for specific timeslot.

        :param dt: time
        :type dt: datetime
        :param interval: interval of one timestep
        :type interval: timedelta
        :return: average fixed load
        :rtype: dict
        """

        # dt: datetime, interval: scenario interval timedelta
        if self.avg_fixed_load is None:
            return 0
        weekday = dt.weekday()
        midnight = dt.replace(hour=0, minute=0)
        timeslot = int((dt - midnight) / interval)
        return self.avg_fixed_load[weekday][timeslot]


class ChargingStation:
    """ChargingStation class"""
    def __init__(self, obj):
        keys = [
            ('max_power', float),
            ('parent', str),
        ]
        optional_keys = [
            ('current_power', float, 0.0),
            ('min_power', float, 0.0)
        ]
        util.set_attr_from_dict(obj, self, keys, optional_keys)


class Photovoltaics:
    """PV power plant class"""
    def __init__(self, obj):
        keys = [
            ('nominal_power', float),
            ('parent', str),
        ]
        optional_keys = []
        util.set_attr_from_dict(obj, self, keys, optional_keys)


class VehicleType:
    """VehicleType class"""
    def __init__(self, obj):
        keys = [
            ('name', str),
            ('capacity', float),
            ('charging_curve', loading_curve.LoadingCurve),
        ]
        optional_keys = [
            ('min_charging_power', float, 0.0),
            ('battery_efficiency', float, 0.95),
            ('v2g', bool, False),
            ('v2g_power_factor', float, 0.5),
            ('discharge_curve', loading_curve.LoadingCurve, None),
            ('loss_rate', float, 0),
        ]
        util.set_attr_from_dict(obj, self, keys, optional_keys)
        max_power = self.charging_curve.max_power
        assert self.min_charging_power <= max_power
        if self.discharge_curve is None:
            # no info: use V2G power factor to scale charging curve for discharging
            self.discharge_curve = self.charging_curve.clamped(
                max_power, pre_scale=self.v2g_power_factor)


class Vehicle:
    """Vehicle class"""
    def __init__(self, obj, vehicle_types):
        keys = [
            ('vehicle_type', vehicle_types.get),
        ]
        optional_keys = [
            ('connected_charging_station', str, None),
            ('estimated_time_of_arrival', util.datetime_from_isoformat, None),
            ('estimated_time_of_departure', util.datetime_from_isoformat, None),
            ('desired_soc', float, 0.),
            ('soc', float, 0.),
            ('schedule', float, None),
        ]
        util.set_attr_from_dict(obj, self, keys, optional_keys)

        # Add battery object to vehicles
        self.battery = battery.Battery(
            capacity=self.vehicle_type.capacity,
            loading_curve=self.vehicle_type.charging_curve,
            soc=self.soc,
            efficiency=self.vehicle_type.battery_efficiency,
            unloading_curve=self.vehicle_type.discharge_curve,
            loss_rate=self.vehicle_type.loss_rate,
        )
        del self.soc

    def get_delta_soc(self):
        """Calculates delta soc

        :return: delta_soc
        :rtype: numeric
        """

        return self.desired_soc - self.battery.soc

    def get_energy_needed(self, full=False):
        """Calculate energy needed to reach desired SoC (positive or zero).

        :param full: True if battery should be charged completely
        :type full: bool
        :return: energy needed to charge battery
        :rtype: numeric
        """

        target_soc = 1 if full else self.desired_soc
        return max(target_soc - self.battery.soc, 0) * self.battery.capacity


class StationaryBattery(battery.Battery):
    """StationaryBattery class"""
    def __init__(self, obj):
        keys = [
            ('charging_curve', loading_curve.LoadingCurve),
            ('parent', str),
        ]
        optional_keys = [
            ('capacity', float, -1.0),
            ('min_charging_power', float, 0.0),
            ('soc', float, 0.0),
            ('efficiency', float, 0.95),
            ('discharge_curve', loading_curve.LoadingCurve, None),
            ('loss_rate', dict, {}),
        ]
        util.set_attr_from_dict(obj, self, keys, optional_keys)
        assert self.min_charging_power <= self.charging_curve.max_power

        battery.Battery.__init__(
            self,
            self.capacity if self.capacity >= 0 else 2**64,  # may be unknown (set unlimited)
            self.charging_curve,
            self.soc,
            self.efficiency,
            self.discharge_curve,
            self.loss_rate,
        )
