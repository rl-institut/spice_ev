# Example signal for a time slot
slot_signal = {
    # ID of grid connector. Can be null.
    # In german: "Netzanschluss-ID"
    "grid_connector_id": 1,

    # When was this signal issued. Prior signals concerning the same time slot
    # are overridden.
    # UTC offset should always be included to avoid ambiguousness.
    "issue_time": "2019-12-31T23:00:00+02:00",

    # Start and end time of this slot.
    # UTC offset should always be included to avoid ambiguousness.
    "start_time": "2020-01-01T00:00:00+02:00",
    "end_time": "2020-01-01T00:15:00+02:00",

    # Maximum available power is limited to the given amount in kW.
    # Can be null to allow maximum available power.
    # In german: "Sperrsignal"
    "max_power": null,

    # Cost polynomial coefficients as an array. Defines a function that maps
    # from kWh to €. Can be of varying size to allow different polynomial
    # degrees. Trailing zeros can be omitted.
    #
    # Structure:
    #  [<absolute offset>, <linear factor>, <quadratic factor>, <cubic factor>, ...]
    #
    # Example 1 (constant function):
    #   [1.0] => f(x) = 1.0
    #   Every kWh costs 1€. 10 kWh cost 10€ in total.
    # Example 2 (linear function):
    #   [0.0, 1.0] => f(x) = 0.0 + 1.0 * x
    #   The cost per kWh rises with the amount of kWh in this time slot.
    #   1 kWh costs 1€. 10 kWh costs 10 € per kWh, which is 100 € in total.
    # Example 3 (quadratic function):
    #   [3.0, 2.0, 1.0] => f(x) = 3.0 + 2.0 * x + 1.0 * x * x
    "cost_polynomial": [1.0, 0.0, 1.0]
}
