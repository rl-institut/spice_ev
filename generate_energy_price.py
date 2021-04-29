#!/usr/bin/env python3

import argparse
import datetime
import math
import random

from netz_elog.util import datetime_from_isoformat, set_options_from_config


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Generate energy price as CSV. These files can be included when generating JSON files.')
    parser.add_argument('output', help='output file name (example_price.csv)')
    parser.add_argument('--start', default='2021-01-04T00:00:00+01:00', help='first start time in isoformat')
    # parser.add_argument('--end', default='2021-01-12T00:00:00', help='last start time in isoformat')
    parser.add_argument('--interval', metavar='H', type=int, default=1, help='number of hours for each timestep (Δt)')
    parser.add_argument('--n-intervals', '-n', type=int, default=24*7, help='number of timesteps')
    # parser.add_argument('--foresight', type=int, default=24, help='number of hours events are known in advance (event time)')
    parser.add_argument('--price-seed', type=int, default=None, help='random seed for energy market prices')
    parser.add_argument('--config', help='Use config file to set arguments')
    args = parser.parse_args()

    set_options_from_config(args, check=False, verbose=False)

    # all prices in ct/kWh
    # min, max and std deviation can be set from config
    min_avg_price = vars(args).get("min_avg_price", 2.7)
    max_avg_price = vars(args).get("max_avg_price", 4.9)
    std_avg_price = vars(args).get("std_avg_price", 1.5)
    # overall average and daily amplitude are derived
    avg_avg_price  = (max_avg_price + min_avg_price)/2
    amp_avg_price = (max_avg_price - min_avg_price)/2

    random.seed(args.price_seed)

    start = datetime_from_isoformat(args.start)
    interval = datetime.timedelta(hours=args.interval)
    # end = start + interval * args.n_intervals

    with open(args.output, 'w') as f:
        #write header
        f.write("date,time,price [ct/kWh]")

        for i in range(args.n_intervals):
            cur_time = i*interval + start

            # avg price depending on hour, greatest at morning and evening (double sine curve)
            avg_price = amp_avg_price * math.sin(-cur_time.hour / 6 * math.pi) + avg_avg_price

            price = random.gauss(avg_price, std_avg_price)

            f.write("\n{},{:02d}:{:02d},{:.2f}".format(cur_time.date().isoformat(),cur_time.hour,cur_time.minute, price))
