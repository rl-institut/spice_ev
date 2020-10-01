from abc import ABC, abstractmethod
from copy import deepcopy

import events

def class_from_str(strategy_name):
    if strategy_name == 'greedy':
        return Greedy
    else:
        raise Exception('unknown strategy with name {}'.format(strategy_name))


class Strategy(ABC):
    """ strategy
    """
    @abstractmethod
    def step(self, events=[]):
        raise NotImplementedError


class Greedy(Strategy):
    def __init__(self, constants):
        self.description = "greedy"
        #TODO generate initial world state from deep copy of constants
        self.world_state = deepcopy(constants)
        self.world_state.price = None
        print(self.description)

    def step(self, event_list=[]):
        for ev in event_list:
            if type(ev) == events.ExternalLoad:
                pass
            elif type(ev) == events.GridOperatorSignal:
                pass
            elif type(ev) == events.VehicleEvent:
                pass
            else:
                raise Exception("Unknown event type: {}".format(ev))
        # print(len(event_list))
        #TODO return list of charging commands, +meta info
        return
