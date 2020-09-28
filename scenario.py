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
    print("yay")
    s = Scenario('test_scenario.json')
