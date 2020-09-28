import argparse
import json

import constants
import events
import strategy


class Scenario:
    """ A scenario
    """
    def __init__(self, json_path):
        j = json.load(open(json_path, 'r'))

        self.constants = constants.Constants(j.get('constants'))
        self.events = events.Events(j.get('events'))
        self.strategy = strategy.Strategy(j.get('strategy'))

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Netz_eLOG modelling')
    parser.add_argument('file', nargs='?', default='test_scenario.json', help='scenario JSON file')
    args = parser.parse_args()
    s = Scenario(args.file)
