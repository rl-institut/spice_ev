import datetime
from src import battery, loading_curve, util
from src.events import ExternalLoad


class Constants:
    """ constants values of a scenario
    """
    def __init__(self, obj):
        self.grid_connectors = dict(
            {k: GridConnector(v) for k, v in obj['grid_connectors'].items()})
        self.charging_stations = dict(
            {k: ChargingStation(v) for k, v in obj['charging_stations'].items()})
        self.vehicle_types = dict(
            {k: VehicleType(v) for k, v in obj['vehicle_types'].items()})
        self.vehicles = dict(
            {k: Vehicle(v, self.vehicle_types) for k, v in obj['vehicles'].items()})
        self.batteries = dict(
            {k: StationaryBattery(v) for k, v in obj.get('batteries', {}).items()})


class GridConnector:
    def __init__(self, obj):
        keys = [
            ('max_power', float),
        ]
        optional_keys = [
            ('current_loads', dict, {}),
            ('cost', dict, {}),
            ('target', float, None),
        ]
        util.set_attr_from_dict(obj, self, keys, optional_keys)
        self.avg_ext_load = None
        self.cur_max_power = self.max_power

    def add_load(self, key, value):
        # add power __value__ to current_loads dict under __key__
        # return updated value
        if key in self.current_loads.keys():
            self.current_loads[key] += value
        else:
            self.current_loads[key] = value
        return self.current_loads[key]

    def get_current_load(self, exclude=[]):
        # get sum of current loads not in exclude list
        current_load = 0
        for key, value in self.current_loads.items():
            if key not in exclude:
                current_load += value
        return current_load

    def add_avg_ext_load_week(self, ext_load_list, interval):
        # Compute average load using EnergyValuesList
        # Each weekday has its own sequence of average values, depending on interval
        # Multiple external loads are added up

        # convert EnergyValuesList to event list
        events = ext_load_list.get_events(None, ExternalLoad, has_perfect_foresight=False)
        events_per_day = int(datetime.timedelta(hours=24) / interval)
        values_by_weekday = [[[] for _ in range(events_per_day)] for _ in range(7)]

        # iterate over event list, to find which external load is present during which interval step
        # take care when EnergyValuesList.step_duration_s != interval (not in sync)
        # last event in interval used, similar to strategy implementation
        cur_time = ext_load_list.start_time - interval
        cur_value = None
        while True:
            cur_time += interval

            if len(events) == 0:
                break

            # get last event for this timestep
            while len(events) > 0 and events[0].start_time <= cur_time:
                event = events.pop(0)
                cur_value = event.value

            # insert external load value into specific timeslot
            if cur_value is not None:
                weekday = cur_time.weekday()
                midnight = cur_time.replace(hour=0, minute=0)
                timeslot = int((cur_time - midnight) / interval)
                values_by_weekday[weekday][timeslot].append(cur_value)

        # compute averages
        avg_values_by_weekday = [[
            (sum(v) / len(v)) if len(v) > 0 else 0 for v in day_values
        ] for day_values in values_by_weekday]

        # set/update avg_ext_load for this GC
        if self.avg_ext_load is None:
            self.avg_ext_load = avg_values_by_weekday
        else:
            # multiple external loads: add up
            for i, values in enumerate(avg_values_by_weekday):
                self.avg_ext_load[i] = [e + v for (e, v) in zip(self.avg_ext_load[i], values)]

    def get_avg_ext_load(self, dt, interval):
        # get average external load for specific timeslot
        # dt: datetime, interval: scenario interval timedelta
        if self.avg_ext_load is None:
            return 0
        weekday = dt.weekday()
        midnight = dt.replace(hour=0, minute=0)
        timeslot = int((dt - midnight) / interval)
        return self.avg_ext_load[weekday][timeslot]


class ChargingStation:
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


class VehicleType:
    def __init__(self, obj):
        keys = [
            ('name', str),
            ('capacity', float),
            ('charging_curve', loading_curve.LoadingCurve),
        ]
        optional_keys = [
            ('min_charging_power', float, 0.0),
            ('battery_efficiency', float, 1.0),
            ('v2g', bool, False),
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
            ('desired_soc', float, 0.),
            ('soc', float, 0.),
        ]
        util.set_attr_from_dict(obj, self, keys, optional_keys)

        # Add battery object to vehicles
        self.battery = battery.Battery(
            capacity=self.vehicle_type.capacity,
            loading_curve=self.vehicle_type.charging_curve,
            soc=self.soc,
            efficiency=self.vehicle_type.battery_efficiency
        )
        del self.soc

    def get_delta_soc(self):
        return self.desired_soc - self.battery.soc

    def get_energy_needed(self, full=False):
        # calculate energy needed to reach desired SoC (positive or zero)
        target_soc = 1 if full else self.desired_soc
        return max(target_soc - self.battery.soc, 0) * self.battery.capacity


class StationaryBattery(battery.Battery):
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
        ]
        util.set_attr_from_dict(obj, self, keys, optional_keys)
        assert self.min_charging_power <= self.charging_curve.max_power

        battery.Battery.__init__(
            self,
            self.capacity if self.capacity >= 0 else 2**64,  # may be unknown (set unlimited)
            self.charging_curve,
            self.soc,
            self.efficiency
        )
