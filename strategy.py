

class Strategy:
    """ strategy
    """
    def __init__(self, strat_string):
        # module = __import__(self.__module__)
        # cls = getattr(module, strat_string.capitalize())
        # cls()
        globals()[strat_string.capitalize()]()

class Greedy(Strategy):
    def __init__(self):
        self.description = "greedy"
        print(self.description)
