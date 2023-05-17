

.. _code:

Code documentation
~~~~~~~~~~~~~~~~~~

Below, the scripts and modules of SpiceEV are explained. The structure of the documentation follows the one from the
GitHub repository.

.. _generate:

Generate
========
The `generate` script and corresponding :ref:`generate modules <generate_modules>` prepare input files for the
simulation with SpiceEV. For an example configuration file see `/examples/configs/generate.cfg`.

.. currentmodule:: generate
.. autosummary::
    :toctree: temp/

    update_namespace
    generate

.. _generate_schedule:

Generate schedule
=================
The `generate_schedule` script, together with the :ref:`generate_schedule module <generate_schedule_module>`, generates
a CSV file containing a schedule from the grid operator containing grid signals. The path to the CSV file is also added
to an existing `scenario.json`. For an example configuration file see `/examples/configs/generate_schedule.cfg`.

Simulate
========
The `simulate` script reads in the simulation input arguments, sets up the scenario and runs the simulation. It serves as a
wrapper for the simulation. For an example configuration file see `/examples/configs/simulate.cfg`.

.. currentmodule:: simulate
.. autosummary::
    :toctree: temp/

    simulate

Calculate costs
===============
The `calculate costs` script calculates the electricity costs based on the price sheet of the respective distribution
grid operator. The calculation can be done during the simulation or independently afterwards. For the latter case a
configuration file is needed. For an example see `/examples/configs/calculate_costs.cfg`.

.. currentmodule:: calculate_costs
.. autosummary::
    :toctree: temp/

    read_simulation_csv

spice_ev
========

The `spice_ev` folder contains the modules called by the scripts above.

.. _generate_modules:

Generate
--------

Depending on the mode selected in the `generate.cfg` one of the following modules is called by the `generate.py`.

Generate_from_csv
.................
This module generates a `scenario.json` from a CSV file with a rotation schedule of the vehicle fleet. For an example
configuration file see `/examples/configs/generate_from_csv.cfg`.

.. currentmodule:: spice_ev.generate.generate_from_csv
.. autosummary::
    :toctree: temp/

    generate_from_csv
    csv_to_dict
    assign_vehicle_id

Generate_from_statistics
........................
This module generates a `scenario.json` with random dummy trips for a set up defined by the input
arguments. For an example configuration file see `/examples/generate_from_statistics.cfg`.

.. currentmodule:: spice_ev.generate.generate_from_statistics
.. autosummary::
    :toctree: temp/

    datetime_from_string
    generate_trip
    generate_from_statistics

Generate_from_simbev
....................
This module generates a `scenario.json` from SimBEV [#]_ results. For an example
configuration file see `/examples/configs/generate_from_simbev.cfg`.

.. currentmodule:: spice_ev.generate.generate_from_simbev
.. autosummary::
    :toctree: temp/

    parse_vehicle_types
    generate_from_simbev

.. _generate_schedule_module:

Generate_schedule
.................

This module generates a schedule with grid signals needed by some charging strategies and incentive schemes. For an
example configuration file see `/examples/configs/generate_schedule.cfg`.

.. currentmodule:: spice_ev.generate.generate_schedule
.. autosummary::
    :toctree: temp/

    generate_flex_band
	generate_individual_flex_band
    generate_schedule

Strategies
----------

Different strategies can be applied on the vehicle fleet. For more information on the strategies see section
:ref:`Charging strategies <charging_strategies>`. Depending on the strategy selected in the `simulate.cfg` one of the
following modules is called by the `simulate.py`.

Balanced
........
Each vehicle is charged such that it uses its complete standing time to reach the
desired state of charge (SOC). May charge more power (and above the desired SOC) if there is
surplus from local generation or if the energy price falls below a certain PRICE_THRESHOLD.

.. currentmodule:: spice_ev.strategies.balanced
.. autosummary::
    :toctree: temp/

    Balanced
    Balanced.step
    load_vehicle

Balanced Market
...............
When using this strategy, price information within the next *HORIZON* hours is evaluated. The goal is to divide standing
times into periods of equal prices. A vehicle is now charged such that is uses the entire duration of all periods with
the lowest price combined to reach its desired SOC. In case that time is not sufficient the periods of the second cheapest
price are used to charge as much of the remaining delta SOC as possible, again in a balanced way with respect to power.

.. currentmodule:: spice_ev.strategies.balanced_market
.. autosummary::
    :toctree: temp/

    BalancedMarket
    BalancedMarket.step

Distributed
...........
Unlimited grid connectors (GCs) are supported. Vehicles that arrive at a charging station with opportunity charging are
charged with strategy `greedy`, those arriving at a depot station are charged with strategy `balanced`. Possible use
cases are bus scenarios.

.. currentmodule:: spice_ev.strategies.distributed
.. autosummary::
    :toctree: temp/

    Distributed
    Distributed.step

Flex window
...........

An attempt is made to charge the vehicles mainly in time windows during which charging is encouraged by the grid
operator. During these time windows the vehicles are charged with a sub-strategy that can be selected in the
`simulate.cfg`

.. currentmodule:: spice_ev.strategies.flex_window
.. autosummary::
    :toctree: temp/

    FlexWindow
    FlexWindow.step
    FlexWindow.distribute_balanced_vehicles
    FlexWindow.distribute_balanced_batteries
    FlexWindow.distribute_balanced_v2g
    FlexWindow.distribute_peak_shaving_vehicles
    FlexWindow.distribute_peak_shaving_batteries
    FlexWindow.distribute_peak_shaving_v2g
    FlexWindow.distribute_power
    FlexWindow.load_surplus_to_batteries

Greedy
......
With this strategy one vehicle after the other is charged with full power until the desired SOC is reached. Depending on
the maximum permitted power at the GC, multiple vehicles may be charged in one timestep.

.. currentmodule:: spice_ev.strategies.greedy
.. autosummary::
    :toctree: temp/

    Greedy
    Greedy.step
    load_vehicle


Peak load window
................
Given a time window of high load, this strategy tries to charge outside of this time window. Different sub-strategies
are supported.

.. currentmodule:: spice_ev.strategies.peak_load_window
.. autosummary::
    :toctree: temp/

    PeakLoadWindow
    PeakLoadWindow.step
    PeakLoadWindow.distribute_power


Schedule
........
This strategy distributes the power according to a schedule given by the respective grid operator.

.. currentmodule:: spice_ev.strategies.schedule
.. autosummary::
    :toctree: temp/

    Schedule
    Schedule.dt_to_end_of_time_window
    Schedule.sim_balanced_charging
    Schedule.collect_future_gc_info
    Schedule.evaluate_core_standing_time_ahead
    Schedule.charge_vehicles_during_core_standing_time
    Schedule.charge_vehicles_during_core_standing_time_v2g
    Schedule.charge_vehicles_after_core_standing_time
    Schedule.charge_vehicles
    Schedule.charge_individually
    Schedule.utilize_stationary_batteries
    Schedule.step


Battery
-------
This module contains the class `Battery` and its methods which describe the behaviour of a vehicle battery.

.. currentmodule:: spice_ev.battery
.. autosummary::
    :toctree: temp/

    Battery
    Battery.load
    Battery.unload
    Battery.load_iterative
    Battery.get_available_power
    Battery._adjust_soc

Components
----------
This module contains all `Components` relevant for the distribution of the electrical energy at a site, except for the
fixed load.

.. currentmodule:: spice_ev.components
.. autosummary::
    :toctree: temp/

    Components
    GridConnector
    GridConnector.add_load
    GridConnector.get_current_load
    GridConnector.add_avg_fixed_load_week
    GridConnector.get_avg_fixed_load
    ChargingStation
    Photovoltaics
    VehicleType
    Vehicle
    Vehicle.get_delta_soc
    Vehicle.get_energy_needed
    StationaryBattery

Costs
-----
This module is called by the `calculate_costs.py` and contains all cost related functions.

.. currentmodule:: spice_ev.costs
.. autosummary::
    :toctree: temp/

    get_flexible_load
    find_prices
    calculate_commodity_costs
    calculate_capacity_costs_rlm
    calculate_costs


Events
------
This module sets up the events for the simulation period.

.. currentmodule:: spice_ev.events
.. autosummary::
    :toctree: temp/

    Events
    Events.get_event_steps
    Event
    LocalEnergyGeneration
    FixedLoad
    EnergyValuesList
    EnergyValuesList.get_events
    GridOperatorSignal
    get_energy_price_list_from_csv
    get_schedule_from_csv
    VehicleEvent


Loading curve
-------------
This module contains the class `LoadingCurve` and its methods which are needed for the batteries.

.. currentmodule:: spice_ev.loading_curve
.. autosummary::
    :toctree: temp/

    LoadingCurve
    LoadingCurve.power_from_soc
    LoadingCurve.clamped
    LoadingCurve.get_section_boundary


Report
------
This module contains functions to aggregates the simulation results, store them in files and visualize them if the
respective options are selected in the `simulate.cfg`.

.. currentmodule:: spice_ev.report
.. autosummary::
    :toctree: temp/

    aggregate_global_results
    aggregate_local_results
    split_feedin
    aggregate_timeseries
    generate_soc_timeseries
    plot
    generate_reports


Scenario
--------
This module is called by the `simulation.py`. It sets up components, events, start time and the interval of the
simulation. The simulation is run stepwise, calling the module strategy for each timestep.

.. currentmodule:: spice_ev.scenario
.. autosummary::
    :toctree: temp/

    Scenario
    Scenario.run


Strategy
--------
This module serves as a wrapper for the individual charging strategies and determines how excess energy is distributed
to the other flexible loads.

.. currentmodule:: spice_ev.strategy
.. autosummary::
    :toctree: temp/

    Strategy
    Strategy.step
    Strategy.distribute_surplus_power


Util
----
This modul contains some utility functions needed by different scripts and modules.

.. currentmodule:: spice_ev.util
.. autosummary::
    :toctree: temp/

    datetime_from_isoformat
    datetime_within_window
    dt_within_core_standing_time
    set_attr_from_dict
    get_cost
    get_power
    clamp_power
    set_options_from_config
    sanitize


.. rubric:: Footnotes

.. [#] https://github.com/rl-institut/simbev
