

class Strategy:
    """ strategy
    """
    def __init__(self, strat_string):
        cls = getattr(self, strat_string.capitalize())
        cls()

    class Greedy():
        def __init__(self):
            self.description = "greedy"
            print("Greedy init")
