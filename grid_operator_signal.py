
class GridOpSlotSignal:
    """ Signal from the network operator that stores the parameters for a time
    slot.

    Parameters
    ----------
    grid_connector_id: int or str
        ID of grid connector. Can be None.
        In german: "Netzanschluss-ID"
    issue_time:
        When was this signal issued. Prior signals concerning the same time slot
        are overridden.
        UTC offset should always be included to avoid ambiguousness.
    start_time:
        Start time of this slot.
        UTC offset should always be included to avoid ambiguousness.
    end_time:
        End time of this slot.
        UTC offset should always be included to avoid ambiguousness.
    max_power: number
        Maximum available power is limited to the given amount in kW.
        Can be null to allow maximum available power.
        In german: "Sperrsignal"
    cost_polynomial: list of numbers
        Cost polynomial coefficients as an array. Defines a function that maps
        from kWh to €. Can be of varying size to allow different polynomial
        degrees. Trailing zeros can be omitted.

        Structure:
         [<absolute offset>, <linear factor>, <quadratic factor>, <cubic factor>, ...]

        Example 1 (constant function):
          [1.0] => f(x) = 1.0
          Every kWh costs 1€. 10 kWh cost 10€ in total.
        Example 2 (linear function):
          [0.0, 1.0] => f(x) = 0.0 + 1.0 * x
          The cost per kWh rises with the amount of kWh in this time slot.
          1 kWh costs 1€. 10 kWh costs 10 € per kWh, which is 100 € in total.
        Example 3 (quadratic function):
          [3.0, 2.0, 1.0] => f(x) = 3.0 + 2.0 * x + 1.0 * x * x

    """
    def __init__(self):
        self.grid_connector_id = None
        self.issue_time = None
        self.start_time = None
        self.end_time = None
        self.max_power = None
        self.cost = {'fixed': None}


# Example signal for a time slot
slot_signal = {
    "grid_connector_id": 1,
    "issue_time": "2019-12-31T23:00:00+02:00",
    "start_time": "2020-01-01T00:00:00+02:00",
    "end_time": "2020-01-01T00:15:00+02:00",
    "max_power": null,
    "cost": {
        'polynomial': [1.0, 0.0, 1.0],
    }
}


# state
{
    # battery electric vehicle
    'bevs': {
        1: {
            'connected_charging_station': 1,
            'estimated_time_of_departure': "2020-01-02T00:08:00+02:00",
            'desired_soc': 90,
            'soc': 100,
            'capacity': 70,
            'max_charging_power': 7,
            # Ladekurve, ...
            '': '...',
        },
        2: {
            'connected_charging_station': None,
            'estimated_time_of_departure': None,
            'soc': 85,
            'capacity': 70,
            'max_charging_power': 22,
        },
    },
    'grid_connector': {
        'max_power': 200,
    }
    'charging_station': {
        1: {
            'max_power': 50,
        },
        2: {
            'max_power': 50,
        },
        3: {
            'max_power': 50,
        },
    },
}

# list of state changes / events
[
    {
        'time': "2020-01-01T13:37:00+02:00",
        'bevs': {
            1: {
                'connected_charging_station': None,
                'estimated_time_of_arrival': "2020-01-01T20:37:00+02:00",
            },
        }
    },
    {
        "time": "2019-12-31T23:00:00+02:00",
        'slot_signal': {
            "grid_connector_id": 1,
            "start_time": "2020-01-01T00:00:00+02:00",
            "end_time": "2020-01-01T00:15:00+02:00",
            "max_power": null,
            "cost": {
                'polynomial': [1.0, 0.0, 1.0]
            }
        }
    },
    {
        "time": "2019-12-31T23:00:00+02:00",
        'slot_signal': {
            "grid_connector_id": 1,
            "start_time": "2020-01-01T00:15:00+02:00",
            "end_time": "2020-01-01T00:30:00+02:00",
            "max_power": null,
            "cost": {
                'fixed': 100,
            }
        }
    }
]
