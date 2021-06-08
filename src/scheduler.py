#!/usr/bin/env python3

import csv
import math


class Scheduler:
    """ A Scheduler
    """

    def __init__(self, time_series):
        self.time_series = time_series

    def get_time_series_indices_for_date_range(self, datetime_from, datetime_until):
        return [index for index, row in enumerate(self.time_series)
                if datetime_from <= row['timestamp'] <= datetime_until]

    def add_value_to_column_by_indices(self, idx, column, value):
        for i in idx:
            self.time_series[i][column] += value

    def spread_flexibility_on_priorities(self, datetime_from, datetime_until,
                                         flexibility, min_load, max_load):
        """
        :param datetime_from: datetime start of flexibility window
        :param datetime_until: datetime end of flexibility window
        :param flexibility: The flexibility in kWh
        :param min_load: The minimal load in the given flexibility window in kW
        :param max_load: The maximal load in the given flexibility window in kW
        """
        min_steps = math.ceil(4 * flexibility / max_load)
        max_steps = math.floor(4 * flexibility / min_load)
        priority = 1
        datetime_idx = self.get_time_series_indices_for_date_range(datetime_from, datetime_until)
        while flexibility > 0:
            if priority > 4:
                raise ValueError('Division of flexibility is impossible')
            idx = [i for i in datetime_idx if self.time_series[i]['priority'] == priority]

            steps = len(idx)
            if steps == 0:
                # No steps found. Flexibility has to be used in other criterias
                pass
            elif min_steps <= steps <= max_steps:
                # The flexibility can be divided equally on all steps without breaking min/max
                # load values
                kilowatt_per_step = 4 * flexibility / steps
                self.add_value_to_column_by_indices(idx, 'signal_kw', kilowatt_per_step)
                return
            elif steps > max_steps:
                # Equally dividing the flexibility would result in loads < min load
                # -> The flexibility is divided on the first x (=max_steps) matching the criteria
                kilowatt_per_step = 4 * flexibility / max_steps
                self.add_value_to_column_by_indices(idx[:max_steps], 'signal_kw', kilowatt_per_step)
                return
            elif steps < min_steps:
                # Equally dividing the flexibility would result in loads > max load
                # -> All matching entries receive the maximum load.
                # The remaining flexibility is further distributed
                used_flexibility = steps / 4 * max_load
                self.add_value_to_column_by_indices(idx, 'signal_kw', max_load)
                flexibility -= used_flexibility
            priority += 1

    def add_flexibility_for_date_and_vehicle_groups(self,
                                                    datetime_from,
                                                    datetime_until,
                                                    vehicle_groups):
        """
        param datetime_from: The start datetime of the flexibility window
        param datetime_until: The end datetime of the flexibility window
        param vehicle_groups: a list of flexibilities containing flexibility (kWh), min load (kW),
        start_time, end_time
        """
        for flexibility, min_load, max_load, start_time, end_time in vehicle_groups:
            self.spread_flexibility_on_priorities(datetime_from, datetime_until, flexibility,
                                                  min_load, max_load)

    def add_percentage_signal(self, max_load):
        for row in self.time_series:
            row['signal_percent'] = row['signal_kw'] / max_load

    def save_schedule(self, filename):
        save_columns = ['iso_datetime', 'signal_kw', 'signal_percent']
        with open(filename, 'w', newline='') as csvfile:
            writer = csv.writer(csvfile, delimiter=',')
            writer.writerow(save_columns)
            for row in self.time_series:
                writer.writerow([
                    row['iso_datetime'],
                    row['signal_kw'],
                    row['signal_percent']
                ])
