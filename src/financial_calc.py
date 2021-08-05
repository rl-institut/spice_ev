import numpy as np

from src.util import get_cost, look_up


class Financial:
    def __init__(self, setup):
        self.eeg_feed_in_points = None
        self.battery_cost_dict = dict()
        self.battery_cost_type = {"type": "polynomial"}
        self.battery_scale = 1000
        if "feed_in" in setup:
            feed_in_kwp = setup['feed_in']['kWp'] + [np.Inf]
            tariff = setup['feed_in']['tariff'] + [None]
            self.eeg_feed_in_points = [(p, rate) for p, rate in zip(feed_in_kwp, tariff)]

        if "eeg" in setup:
            self.eeg_levy = setup['eeg']['levy']
            self.eeg_kwp_thr = setup['eeg']['selfconsumption_kWp_thr']
            self.eeg_self_cost = self.eeg_levy * setup['eeg']['selfconsumption_pct']



    def eeg_feed_in(self, energy, peak_power, ct=100):
        profit_ct = energy * look_up(peak_power, self.eeg_feed_in_points, type='step')

        return profit_ct / ct

    def eeg_cost(self, energy, peak_power):
        # Returns zero until kWp threshold and returns reduced eeg cost for self-consumed energy
        if peak_power <= self.eeg_kwp_thr:
            cost = 0
        else:
            cost = energy * self.eeg_self_cost

        return cost

    def setup_smooth_capex(self, capex_dict):
        # TODO setup calculation that is compatible to smooth dictionaries
        pass

    def battery_cost(self, capacity, c_rate=1):
        # Currently hard coded from smooth example configuration values, dictionary excerpt in:
        # examples.cost_example.cost_parameter_capex
        # could potentially setup in class class function setup_smooth_capex

        self.battery_cost_dict[0] = {
            "value": [c_rate, 2109.62368 / 1e3, -147.52325 / 1e6, 6.97016 / 1e9, -0.13996 / 1e12, 0.00102 / 1e15]}
        self.battery_cost_dict[50] = {"value": [c_rate, 1000.2 / 1e3, -0.4983 / 1e6]}
        self.battery_cost_dict[1000] = {"value": [capacity * self.battery_scale, 0.353, 0.149]}

        if capacity < 50:
            cost = get_cost(capacity * self.battery_scale, {**self.battery_cost_dict[0], **self.battery_cost_type})
        elif capacity < 1000:
            cost = get_cost(capacity * self.battery_scale, {**self.battery_cost_dict[50], **self.battery_cost_type})
        elif capacity >= 1000:
            # TODO: last intervall has offset/scaling issue
            cost = get_cost(1, {**self.battery_cost_dict[1000], **self.battery_cost_type})
        else:
            cost = None

        return cost

    def grid_cost(self, sc):
        # TODO
        return [(i, val.max_power) for i, val  in sc.constants.grid_connectors.items()]

    def capex_cost(self, sc):
        # TODO
        pass

    def price_structure(self):
        cost = None

        return cost
