import argparse
import datetime
import json

import constants
import events
import strategy
import util


class Scenario:
    """ A scenario
    """
    def __init__(self, json_dict):
        self.constants = constants.Constants(json_dict.get('constants'))
        self.events = events.Events(json_dict.get('events'))
        self.strategy = strategy.Strategy(json_dict.get('strategy'))

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

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Netz_eLOG modelling')
    parser.add_argument('file', nargs='?', default='test_scenario.json', help='scenario JSON file')
    args = parser.parse_args()
    with open(args.file, 'r') as f:
        s = Scenario(json.load(f))
