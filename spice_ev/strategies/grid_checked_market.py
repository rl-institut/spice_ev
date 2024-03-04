# from spice_ev.strategy import Strategy
from spice_ev.strategies.balanced_market import BalancedMarket


class GridCheckedMarket(BalancedMarket):
    """ Price oriented charging at times of low energy price. """
    def __init__(self, components, start_time, **kwargs):
        super().__init__(components, start_time, **kwargs)
        self.description = "grid checked market"

    def step(self):
        """ Calculate charging power in each timestep.

        :return: current time and commands of the charging stations
        :rtype: dict
        """
        commands = super().step()["commands"]
        for gcID, gc in self.world_state.grid_connectors.items():
            if gc.capacity is not None and gc.get_current_load() > gc.capacity:
                print(self.current_time, f"{gcID} capacity exceeded")
        return {'current_time': self.current_time, 'commands': commands}
