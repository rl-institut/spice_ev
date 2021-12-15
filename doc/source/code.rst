

.. _code:

Code documentation
~~~~~~~~~~~~~~~~~~

.. _generate:

Generate inputs
===============

Generate
--------
.. currentmodule:: generate
.. autosummary::
    :toctree: temp/

    generate.generate_trip
    generate.generate

Generate_energy_price
---------------------
.. currentmodule:: generate_energy_price
.. autosummary::
    :toctree: temp/

    generate_energy_price.generate_energy_price

Generate_from_csv
-----------------
.. currentmodule:: generate_from_csv
.. autosummary::
    :toctree: temp/

    generate_from_csv.generate_from_csv
    generate_from_csv.get_number_vehicles_per_vehicle_type
    generate_from_csv.csv_to_dict

Generate_from_download
----------------------

.. currentmodule:: generate_from_download
.. autosummary::
    :toctree: temp/

    generate_from_download.generate_from_download


Generate_from_simbev
--------------------

.. currentmodule:: generate_from_simbev
.. autosummary::
    :toctree: temp/

    generate_from_simbev.generate_from_simbev

Generate_schedule
-----------------
.. currentmodule:: generate_schedule
.. autosummary::
    :toctree: temp/

    generate_schedule.generate_schedule
    generate_schedule.generate_flex_band


Generate_fixed_schedule_for_scenario
------------------------------------
.. currentmodule:: generate_fixed_schedule_for_scenario
.. autosummary::
    :toctree: temp/

Simulate
========
Reads in simulation input arguments, sets up scenario and runs the simulation.
Functions as a wrapper for the simulation.

.. currentmodule:: simulate
.. autosummary::
    :toctree: temp/

    simulate.simulate

Scenario
========
Sets up constants, events, start time, interval of the simlation. Runs simulation
stepwise and calls charging strategy for each timestep.

.. currentmodule:: src.scenario
.. autosummary::
    :toctree: temp/

    Scenario
    Scenario.run



Charging strategies
===================
Wrapper / Parent class for the individual strategies.

.. currentmodule:: src.strategy
.. autosummary::
    :toctree: temp/

    Strategy
    Strategy.step

Balanced
--------
Each car is charged such that it uses its complete standing time to reach the
desired SoC. May charge more power (and above the desired SoC) if there is
surplus feed-in power or if the energy price falls below a certain PRICE_THRESHOLD.

.. currentmodule:: src.strategies.balanced
.. autosummary::
    :toctree: temp/

    Balanced
    Balanced.step

Balanced Market
---------------
When using this strategy, price information within the next *HORIZON* hours is evaluated. The goal is to divide standing times into periods of equal prices. A vehicle is now charged such that is uses the entire duration of all periods with the lowest price combined to reach its desired SOC. In case that time is not sufficient the periods of the second cheapest price are used to charge as much of the remaining delta SOC as possible, again in a balanced way with respect to power.


.. currentmodule:: src.strategies.balanced_market
.. autosummary::
    :toctree: temp/

    BalancedMarket
    BalancedMarket.step

Flex window
-----------
There are time windows during which charging is encouraged and there are those where it is discouraged. These time windows are determined by the grid operator (similar to Schedule strategy). During those windows where charging is encouraged the vehicles are charged with a sub-strategy.

.. currentmodule:: src.strategies.flex_window
.. autosummary::
    :toctree: temp/

    FlexWindow
    FlexWindow.step

Greedy
------
Charges one vehicle after the next with full power until the desired state of
charge (SoC) is reached. Depending on the grid connector (GC), multiple cars
may be charged in one timestep.

.. currentmodule:: src.strategies.greedy
.. autosummary::
    :toctree: temp/

    Greedy
    Greedy.step

Greedy Market
-------------
This algorithm first determines the cheapest group of time intervals sufficient to charge all vehicles according to their needs.
Moves all charging events to those time intervals and charges them with full power, similar to the greedy strategy. Only one grid connector supported.

.. currentmodule:: src.strategies.greedy_market
.. autosummary::
    :toctree: temp/

    GreedyMarket
    GreedyMarket.step

Inverse
-------
Charging strategy that prioritizes times with lower power costs. The idea is to find the minimum viable cost threshold (per car or for the whole fleet). This way, timesteps with less external load and smaller costs are prioritized for loading. In times with low cost, the maximum available power is used, no computation needed.

.. currentmodule:: src.strategies.inverse
.. autosummary::
    :toctree: temp/

    Inverse
    Inverse.step

Peak load window
----------------
Given a time window of high load, tries to charge outside this window. Different sub-strategies supported

.. currentmodule:: src.strategies.peak_load_window
.. autosummary::
    :toctree: temp/

    PeakLoadWindow
    PeakLoadWindow.step
    PeakLoadWindow.distribute_power

Schedule
--------
Allocate power according to grid operator schedule.

.. currentmodule:: src.strategies.schedule
.. autosummary::
    :toctree: temp/

    Schedule
    Schedule.dt_to_end_of_time_window
    Schedule.sim_balanced_charging
    Schedule.collect_future_gc_info
    Schedule.evaluate_core_standing_time_ahead
    Schedule.charge_cars_during_core_standing_time
    Schedule.charge_cars_after_core_standing_time
    Schedule.charge_cars
    Schedule.utilize_stationary_batteries
    Schedule.step

Schedule foresight
------------------
ScheduleForesight looks into the future (until all cars have left, at most 24h) and tries to adjust schedule so that all cars can be charged for the next trip. Implements different sub-strategies:--------

.. currentmodule:: src.strategies.schedule_foresight
.. autosummary::
    :toctree: temp/

    ScheduleForesight

v2g
---
.. currentmodule:: src.strategies.v2g
.. autosummary::
    :toctree: temp/

    V2g

Components
==========

.. currentmodule:: src.battery
.. autosummary::
    :toctree: temp/

    Battery
    Battery.load
    Battery.unload
    Battery.load_iterative
    Battery.get_available_power

.. currentmodule:: src.constants
.. autosummary::
    :toctree: temp/

    StationaryBattery
    GridConnector
    GridConnector.add_load
    GridConnector.get_current_load
    GridConnector.add_avg_ext_load_week
    GridConnector.get_avg_ext_load
    ChargingStation
    VehicleType
    Vehicle
    Vehicle.get_delta_soc
    Vehicle.get_energy_needed


Events
======
.. currentmodule:: src.events
.. autosummary::
    :toctree: temp/

    Event
    Events
    Events.get_event_steps
    EnergyFeedIn
    ExternalLoad
    EnergyValuesList
    EnergyValuesList.get_events
    GridOperatorSignal
    VehicleEvent

    get_energy_price_list_from_csv
    get_schedule_from_csv


Loading curve
=============
.. currentmodule:: src.loading_curve
.. autosummary::
    :toctree: temp/

    LoadingCurve
    LoadingCurve.power_from_soc
    LoadingCurve.clamped

Util
====
Utility functions.

.. currentmodule:: src.util
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
