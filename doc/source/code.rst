

.. _code:

Code documentation
~~~~~~~~~~~~~~~~~~

.. _generate:

Generate inputs
===============
The generate scripts prepare input files for the simulation with SpiceEV. Most
generate scripts create a scenario json, containing information about the vehicles, the trips,
the charging stations, grid connectors as well as optional inputs such as
stationary batteries, fixed load, local power plants or grid signal time series. The module
'generate_schedule' prepares a csv file of grid operator schedules and adds this schedule to an existing input json.

Generate_from_statistics
------------------------
Generates a scenario JSON with random dummy trips for a set up defined by input
arguments. For an example config file see `/examples/generate_from_statistics.cfg`.

.. currentmodule:: spice_ev.generate.generate_from_statistics
.. autosummary::
    :toctree: temp/

    generate_trip
    generate_from_statistics

Generate_from_csv
-----------------
Generates a scenario JSON from csv rotation schedule of fleets. For an example
config file see `/examples/configs/generate_from_csv.cfg`.

.. currentmodule:: spice_ev.generate.generate_from_csv
.. autosummary::
    :toctree: temp/

    generate_from_csv
    csv_to_dict
    assign_vehicle_id

Generate_from_simbev
--------------------
Generates a scenario JSON from simBEV results. For an example
config file see `/examples/configs/generate_from_simbev.cfg`.

.. currentmodule:: spice_ev.generate.generate_from_simbev
.. autosummary::
    :toctree: temp/

    generate_from_simbev

Generate_schedule
-----------------
Generates schedule for grid signals. For an example
config file see `/examples/configs/generate_schedule.cfg`.

.. currentmodule:: spice_ev.generate.generate_schedule
.. autosummary::
    :toctree: temp/

    generate_schedule
    generate_flex_band
	generate_individual_flex_band

Simulate
========
Reads in simulation input arguments, sets up scenario and runs the simulation.
Functions as a wrapper for the simulation. For an example
config file see `/examples/configs/simulate.cfg`.

.. currentmodule:: simulate
.. autosummary::
    :toctree: temp/

    simulate

Scenario
========
Sets up components, events, start time, interval of the simulation. Runs simulation
stepwise and calls charging strategy for each timestep.

.. currentmodule:: spice_ev.scenario
.. autosummary::
    :toctree: temp/

    Scenario
    Scenario.run



Charging strategies
===================
Wrapper / Parent class for the individual strategies.

.. currentmodule:: spice_ev.strategy
.. autosummary::
    :toctree: temp/

    Strategy
    Strategy.step

Balanced
--------
Each vehicle is charged such that it uses its complete standing time to reach the
desired SoC. May charge more power (and above the desired SoC) if there is
surplus from local generation or if the energy price falls below a certain PRICE_THRESHOLD.

.. currentmodule:: spice_ev.strategies.balanced
.. autosummary::
    :toctree: temp/

    Balanced
    Balanced.step

Balanced Market
---------------
When using this strategy, price information within the next *HORIZON* hours is evaluated. The goal is to divide standing times into periods of equal prices. A vehicle is now charged such that is uses the entire duration of all periods with the lowest price combined to reach its desired SOC. In case that time is not sufficient the periods of the second cheapest price are used to charge as much of the remaining delta SOC as possible, again in a balanced way with respect to power.


.. currentmodule:: spice_ev.strategies.balanced_market
.. autosummary::
    :toctree: temp/

    BalancedMarket
    BalancedMarket.step

Flex window
-----------
There are time windows during which charging is encouraged and there are those where it is discouraged. These time windows are determined by the grid operator (similar to Schedule strategy). During those windows where charging is encouraged the vehicles are charged with a sub-strategy.

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

Greedy
------
Charges one vehicle after the next with full power until the desired state of
charge (SoC) is reached. Depending on the grid connector (GC), multiple vehicles
may be charged in one timestep.

.. currentmodule:: spice_ev.strategies.greedy
.. autosummary::
    :toctree: temp/

    Greedy
    Greedy.step

Greedy Market
-------------
This algorithm first determines the cheapest group of time intervals sufficient to charge all vehicles according to their needs.
Moves all charging events to those time intervals and charges them with full power, similar to the greedy strategy.

.. currentmodule:: spice_ev.strategies.greedy_market
.. autosummary::
    :toctree: temp/

    GreedyMarket
    GreedyMarket.step


Peak load window
----------------
Given a time window of high load, tries to charge outside this window. Different sub-strategies supported

.. currentmodule:: spice_ev.strategies.peak_load_window
.. autosummary::
    :toctree: temp/

    PeakLoadWindow
    PeakLoadWindow.step
    PeakLoadWindow.distribute_power


Distributed
-----------
Unlimited grid connectors are supported. Vehicles that arrive at a station with opp (opportunity charging) are loaded with greedy strategy, ones that arrive at a depot station are loaded with balanced strategy.
Application case: Bus scenarios.

.. currentmodule:: spice_ev.strategies.distributed
.. autosummary::
    :toctree: temp/

    Distributed
    Distributed.step

Schedule
--------
Allocate power according to grid operator schedule.

.. currentmodule:: spice_ev.strategies.schedule
.. autosummary::
    :toctree: temp/

    Schedule
    Schedule.dt_to_end_of_time_window
    Schedule.sim_balanced_charging
    Schedule.collect_future_gc_info
    Schedule.evaluate_core_standing_time_ahead
    Schedule.charge_vehicles_during_core_standing_time
    Schedule.charge_vehicles_after_core_standing_time
    Schedule.charge_vehicles
    Schedule.utilize_stationary_batteries
    Schedule.step



Components
==========

.. currentmodule:: spice_ev.battery
.. autosummary::
    :toctree: temp/

    Battery
    Battery.load
    Battery.unload
    Battery.load_iterative
    Battery.get_available_power

.. currentmodule:: spice_ev.components
.. autosummary::
    :toctree: temp/

    StationaryBattery
    GridConnector
    GridConnector.add_load
    GridConnector.get_current_load
    GridConnector.add_avg_fixed_load_week
    GridConnector.get_avg_fixed_load
    ChargingStation
    VehicleType
    Vehicle
    Vehicle.get_delta_soc
    Vehicle.get_energy_needed


Events
======
.. currentmodule:: spice_ev.events
.. autosummary::
    :toctree: temp/

    Event
    Events
    Events.get_event_steps
    LocalEnergyGeneration
    FixedLoad
    EnergyValuesList
    EnergyValuesList.get_events
    GridOperatorSignal
    VehicleEvent

    get_energy_price_list_from_csv
    get_schedule_from_csv


Loading curve
=============
.. currentmodule:: spice_ev.loading_curve
.. autosummary::
    :toctree: temp/

    LoadingCurve
    LoadingCurve.power_from_soc
    LoadingCurve.clamped

Util
====
Utility functions.

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

Calculate costs
===============
Calculate fixed and operating costs based on price sheet. Can be done during simulation or independently afterwards. For an example config file see `/examples/configs/calculate_costs.cfg`.

.. currentmodule:: spice_ev.costs
.. autosummary::
    :toctree: temp/

    calculate_costs
