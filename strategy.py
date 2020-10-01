from abc import ABC, abstractmethod


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
        self.world_state = None
        print(self.description)

    def step(self, events=[]):
        # print(len(events))
        #TODO return list of charging commands, +meta info
        return
