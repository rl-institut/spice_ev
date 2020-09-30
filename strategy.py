

class Strategy:
    """ strategy
    """
    def __init__(self, strat_string, constants):
        # module = __import__(self.__module__)
        # cls = getattr(module, strat_string.capitalize())
        # cls()
        globals()[strat_string.capitalize()](constants)

class Greedy(Strategy):
    def __init__(self, constants):
        self.description = "greedy"
        self.world_state = None
        print(self.description)

    def step(events=[]):
        pass
