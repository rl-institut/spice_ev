#!/usr/bin/env python3

import datetime
import math


def add_priority(row, max_network_load, max_load_range):
    if row['abregelung'] < 0:
        # Highest priority when eeg-plants are shut down
        return 1
    if row['brutto'] < 0:
        # second highest priority when the load is smaller than the feed in
        return 2
    if row['brutto'] > (1 - max_load_range) * max_network_load:
        # Lowest priority when the load is already high
        return 4
    # In all other cases
    return 3


class Scheduler:
    """ A scenario
    """

    def __init__(self, time_series_df, max_load_range, max_load_total):
        # Remove duplicate indices if there are any
        time_series_df = time_series_df.groupby(time_series_df.index).first()

        # Add empty signal column
        time_series_df['signal_kw'] = 0
        time_series_df['iso_datetime'] = [dt.replace(
            tzinfo=datetime.timezone(datetime.timedelta(hours=2))).isoformat() for dt in time_series_df.index]
        # Get the maximum load of the year
        max_load_edis = time_series_df['brutto'].max()
        # Add the priority to the dataframe in ascending order (lowest->highest priority)
        time_series_df['priority'] = time_series_df.apply(add_priority, args=(max_load_edis, max_load_range), axis=1)
        self.time_series_df = time_series_df
        self.max_load_total = max_load_total

    def get_date_filter(self, start_date, start_time, end_time):
        if start_time >= end_time:
            end_date = start_date + datetime.timedelta(days=1)
        else:
            end_date = start_date
        return (self.time_series_df.index >= f'{start_date} {start_time}') & (
                    self.time_series_df.index <= f'{end_date} {end_time}')

    def spread_flexibility_on_priorities(self, datefilter, flexibility, min_load, max_load):
        """
        :param datefilter: The flexibility window as a pandas filter
        :param flexibility: The flexibility in kWh
        :param min_load: The minimal load in the given flexibility window in kW
        :param max_load: The maximal load in the given flexibility window in kW
        :return: The remaining flexibility in kWh
        """
        min_steps = math.ceil(4 * flexibility / max_load)
        max_steps = math.floor(4 * flexibility / min_load)
        priority = 1
        while flexibility > 0:
            if priority > 4:
                raise ValueError('Division of flexibility is impossible')
            idx = self.time_series_df.index[datefilter & (self.time_series_df['priority'] == priority)].tolist()
            steps = len(idx)
            if steps == 0:
                # No steps found. Flexibility has to be used in other criterias
                pass
            elif min_steps <= steps <= max_steps:
                # The flexibility can be divided equally on all steps without breaking min/max load values
                self.time_series_df.loc[idx, 'signal_kw'] += 4 * flexibility / steps
                return
            elif steps > max_steps:
                # Equally dividing the flexibility would result in loads < min load
                # -> The flexibility is divided on the first x (=max_steps) matching the criteria
                self.time_series_df.loc[idx[:max_steps], 'signal_kw'] += 4 * flexibility / max_steps
                return
            elif steps < min_steps:
                # Equally dividing the flexibility would result in loads > max load
                # -> All matching entries receive the maximum load. The remaining flexibility is returned
                used_flexibility = steps / 4 * max_load
                self.time_series_df.loc[idx, 'signal_kw'] += max_load
                flexibility -= used_flexibility
            priority += 1

    def add_flexibility_for_date_and_vehicle_groups(self, date, vehicle_groups):
        """
        param date: The start date of the flexibility window
        param vehicle_groups: a list of flexibilities containing flexibility (kWh), min load (kW), start_time, end_time
        """
        for flexibility, min_load, max_load, start_time, end_time in vehicle_groups:
            date_filter = self.get_date_filter(date, start_time, end_time)
            self.spread_flexibility_on_priorities(date_filter, flexibility,
                                                  min_load, max_load)

        self.time_series_df['signal_percent'] = self.time_series_df['signal_kw'] / self.max_load_total

    def save_schedule(self, filename):
        self.time_series_df[['iso_datetime', 'signal_kw', 'signal_percent']].to_csv(filename, index=False)
