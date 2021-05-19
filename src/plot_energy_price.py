#!/usr/bin/env python3

import argparse
import datetime
import json
from matplotlib import pyplot as plt
import matplotlib.dates as mdates
import matplotlib.ticker as mticker
from mpl_toolkits.axes_grid1 import make_axes_locatable
import numpy
import os

from src.scenario import Scenario
import src.events as events
from src.util import get_cost

if __name__ == '__main__':

    parser = argparse.ArgumentParser(
        description='Show annual energy price from generated JSON file')
    parser.add_argument('file', help='scenario JSON file')
    args = parser.parse_args()

    # create scenario from file
    with open(args.file, 'r') as f:
        s_json = json.load(f)
        s = Scenario(s_json, os.path.dirname(args.file))

    # get all events
    event_steps = s.events.get_event_steps(s.start_time, s.n_intervals, s.interval)
    # some multipliers
    intervals_per_day = datetime.timedelta(days=1) / s.interval
    intervals_per_day = int(intervals_per_day)
    intervals_per_hour = int(intervals_per_day/24)
    days = s.n_intervals / intervals_per_day
    days = int(days)

    gcs = s.constants.grid_connectors
    # init price matrix
    prices = numpy.zeros((len(gcs), days, intervals_per_day), dtype=float)

    cur_time = s.start_time - s.interval
    grid_op_signals = []
    # get costs for all timesteps
    for day in range(days):
        # cycle through day
        for idx in range(intervals_per_day):
            # next timestep
            cur_time += s.interval

            # get events for this timestep
            ts_events = event_steps.pop(0)
            for event in ts_events:
                # append all grid op signals, ignore everything else
                if type(event) == events.GridOperatorSignal:
                    grid_op_signals.append(event)
            # need grid op signals in order
            grid_op_signals.sort(key=lambda ev: ev.start_time)

            # peek into signals to update GC info
            while True:
                try:
                    event = grid_op_signals.pop(0)
                except IndexError:
                    # no signals
                    break
                if event.start_time > cur_time:
                    # oops, event in future -> prepend again
                    grid_op_signals.append(event)
                    break
                gc = gcs[event.grid_connector_id]
                gc.cost = event.cost
                # always use theoretical max power of GC for comparison
                # max_power = event.max_power or gc.max_power
                # gc.cur_max_power = min(max_power, gc.max_power)

            # get max cost per gc
            for gc_idx, gc_id in enumerate(sorted(gcs.keys())):
                gc = gcs[gc_id]
                # cost = get_cost(gc.cur_max_power, gc.cost)
                cost = get_cost(gc.max_power, gc.cost)
                cost = cost / gc.max_power
                prices[gc_idx][day][idx] = cost

    # plot all GC prices
    fig, axes = plt.subplots(len(gcs))
    start_date = s.start_time.date()
    end_date = (s.start_time + s.n_intervals * s.interval).date()
    for gc_idx, gc_id in enumerate(sorted(gcs.keys())):
        if len(gcs) > 1:
            ax = axes[gc_idx]
        else:
            # single plot
            ax = axes

        # get plot limits
        # xaxis: dates -> convert dates to numbers
        xlims = [mdates.date2num(start_date), mdates.date2num(end_date)]
        # yaxis: times -> use interval index
        ylims = [0, intervals_per_day]
        extent = [xlims[0], xlims[1], ylims[1], ylims[0]]
        # show heatmap
        im = ax.imshow(prices[gc_idx].T, cmap='coolwarm', aspect='auto', extent=extent)
        # axes labels
        # tell pyplot xaxis are dates
        ax.xaxis_date()
        # reconvert numbers to dates, show "day. mon"
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%d. %b'))
        # show nice dates (angles)
        fig.autofmt_xdate()

        # reconvert interval indices to hours:minutes
        ax.yaxis.set_major_formatter(
            mticker.FuncFormatter(
                lambda y, pos: "{:02d}:{:02d}".format(
                    int(y//intervals_per_hour),
                    int((y % intervals_per_hour)*(60 / intervals_per_hour))
                )
            )
        )

        ax.set_title(gc_id)

        # show legend in same height as plot
        divider = make_axes_locatable(ax)
        cax = divider.append_axes("right", size="5%", pad=0.05)
        # restrict labels to two decimal places
        fmt2dec = mticker.FuncFormatter(lambda x, pos: "{:.2f}".format(x))
        cbar = plt.colorbar(im, cax=cax, format=fmt2dec)
        # label legend
        cbar.set_label('Strompreis in €/kWh')

    fig.tight_layout()
    plt.show()
