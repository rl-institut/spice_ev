

class Strategy:
    """ strategy
    """
    def __init__(self, strat_string, constants):
        # module = __import__(self.__module__)
        # cls = getattr(module, strat_string.capitalize())
        # cls()
        self = globals()[strat_string.capitalize()](constants)

    def step(events=[]):
        pass

class Greedy(Strategy):
    def __init__(self, constants):
        self.description = "greedy"
        self.world_state = None
        print(self.description)

    def step(events=[]):
        print(len(events))
