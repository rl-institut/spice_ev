#!/usr/bin/env python3

import argparse
import copy
from matplotlib import pyplot as plt
from datetime import timedelta

from spice_ev.src.battery import Battery
from spice_ev.src.loading_curve import LoadingCurve

if __name__ == '__main__':

    parser = argparse.ArgumentParser(description='Show energy load from CSV files')
    parser.add_argument('--timesteps', '-n', type=int, default=200,
                        help='number of simulation timesteps')
    parser.add_argument('--resolution', '-t', type=int, default=15,
                        help='resolution of timestep in minutes')
    args = parser.parse_args()

    capacity = 150
    charging_curve = LoadingCurve([[0, 11], [0.8, 11], [1, 0]])
    soc = 0
    efficiency = 0.5
    battery = Battery(capacity, charging_curve, soc, efficiency)
    compare = copy.deepcopy(battery)

    pwr1 = 0
    pwr2 = 0

    socs = []
    td = timedelta(minutes=args.resolution)
    ts_per_hour = timedelta(hours=1) / td
    for i in range(args.timesteps):
        pwr1 += battery.load(td, battery.loading_curve.max_power)["avg_power"] / ts_per_hour
        pwr2 += compare.load_iterative(
            td, compare.loading_curve.max_power)["avg_power"] / ts_per_hour
        socs.append([battery.soc, compare.soc])

    print("computed: {} kW, iterative: {} kW".format(pwr1, pwr2))

    fig, axes = plt.subplots()
    lines = axes.plot(socs)
    axes.legend(lines, ["simulate", "iterative"])
    plt.show()
