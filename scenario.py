import argparse
import datetime
import json
import os

import constants
import events
import strategy
import util


class Scenario:
    """ A scenario
    """
    def __init__(self, json_dict, dir_path=''):
        self.constants = constants.Constants(json_dict.get('constants'))
        self.events = events.Events(json_dict.get('events'), dir_path, self.constants)

        scenario = json_dict.get('scenario')

        self.start_time = util.datetime_from_isoformat(scenario['start_time'])
        self.interval =  datetime.timedelta(minutes=scenario['interval'])

        # compute n_intervals or stop_time
        assert (scenario.get('stop_time') != None) ^ (scenario.get('n_intervals') != None), 'just one of\'em plz'
        if 'n_intervals' in scenario:
            self.n_intervals = scenario['n_intervals']
            self.stop_time = self.start_time + self.interval * self.n_intervals
        else:
            stop_time = util.datetime_from_isoformat(scenario['stop_time'])
            delta = stop_time - self.start_time
            self.n_intervals = delta / self.interval


    def run(self, strategy_name):
        strat = strategy.class_from_str(strategy_name)(self.constants)

        event_steps = self.events.get_event_steps(self.start_time, self.n_intervals, self.interval)

        current_time = self.start_time

        for step_i in range(self.n_intervals):
            print('step {}: {}'.format(step_i, current_time))
            strat.step(event_steps[step_i])
            current_time += self.interval

        #TODO visualization?


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Netz_eLOG modelling')
    parser.add_argument('file', nargs='?', default='tests/test_scenario.json', help='scenario JSON file')
    parser.add_argument('--strategy', '-s', nargs='?', default='greedy', help='specify strategy for simulation')
    args = parser.parse_args()

    # Read JSON
    with open(args.file, 'r') as f:
        s = Scenario(json.load(f), os.path.dirname(args.file))

    # RUN!
    s.run(args.strategy)
