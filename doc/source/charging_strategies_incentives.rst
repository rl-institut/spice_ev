.. _charging_strategies:

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Charging strategies and incentives
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Charging strategies
===================

The core of SpiceEV are the different charging strategies. They decide how to react to events, when to charge the
vehicles and by how much. To see how to set strategy options, refer to
:ref:`Command line options <command_line_options>`. The following table indicates whether a charging strategy considers
stationary batteries, V2G or local generation.

+--------------------------+-----------------------------+-------------------------------+-------------------------------+
|**Charging strategy**     | **Stationary batteries**    | **V2G**                       |  **Local generation**         |
+--------------------------+-----------------------------+-------------------------------+-------------------------------+
| Greedy                   | x                           | x                             |  x                            |
+--------------------------+-----------------------------+-------------------------------+-------------------------------+
| Balanced                 | x                           | x                             |  x                            |
+--------------------------+-----------------------------+-------------------------------+-------------------------------+
| Balanced market          | x                           | x                             |  x                            |
+--------------------------+-----------------------------+-------------------------------+-------------------------------+
| Schedule                 | x                           | x                             |  x                            |
+--------------------------+-----------------------------+-------------------------------+-------------------------------+
| Peak load window         | x                           |                               |  x                            |
+--------------------------+-----------------------------+-------------------------------+-------------------------------+
| Flex window              | x                           | x                             |  x                            |
+--------------------------+-----------------------------+-------------------------------+-------------------------------+
| Distributed              | x                           | x                             |  x                            |
+--------------------------+-----------------------------+-------------------------------+-------------------------------+

Greedy
------
As soon as a vehicle is connected, it is charged at the maximum possible power until the desired state of charge (SOC) is reached.
Depending on the grid connector (GC), the power must be throttled in order to not exceed its maximum power. A vehicle
may be charged above the desired SOC if there is energy surplus from local generation or the energy price falls below the set price threshold.

Balanced
--------
Each vehicle is charged with the minimal possible power over its standing time to reach the desired SOC. A vehicle
may be charged above the desired SOC if there is energy surplus from local generation or the energy price falls below the set price threshold.
A prerequisite for this strategy is an estimate of the standing time. In the simulation model, a perfect foresight is used for
this purpose. By defining a time horizon, it is possible to specify how far in the future departure times are known.

Balanced market
---------------
Similar to the previous strategy, the charging of the vehicles depends on a given external price time series. A vehicle
is charged such that is uses the entire duration of all periods with the lowest price to reach the desired SOC, whereby
the minimum possible charging power of each vehicle type is also taken into account. In case that time is not sufficient
the periods of the next higher price are used for charging, too.

Schedule
--------
The distribution network operator sends an individual "charging schedule" to the connection users which contains the
time and amount of the total power to be called up for the grid connection. The charging schedule is based on the
flexibility potential of the connection users (total demand for electrical energy, maximum total power of the location
and the core standing time of the vehicle fleet (only for "collective") as well as the expected grid situation.
The core standing time is a fixed period of time during which all vehicles are guaranteed to be available.
Two different sub-strategies can be used:

- Collective: All vehicles are controlled as a unit.
- Individual: All vehicles are controlled individually.

Peak load window
----------------
Given a time window of high load, this strategy tries to charge outside of this window. Different sub-strategies are
supported:

- Greedy: The vehicles charge as much as possible, one after the other (vehicles below desired SOC charge first).
- Needy: The power is allocated according to the missing power needed to reach the desired SOC.
- Balanced: The power is distributed evenly among the vehicles below the desired SOC. Surplus is then distributed evenly
  among all vehicles.

Flex Window
-----------
This strategy uses time windows during which charging is encouraged and those where it is discouraged. The time windows
are determined by the grid operator (similar to `schedule` strategy). During time windows where charging is encouraged
the vehicles are charged with one of the following sub-strategies:

- Greedy: The vehicles that are below their desired SOC are charged one after the other, the rest is ordered by time of
  departure. Vehicles with earlier departures are charged first.
- Needy: The vehicles with little energy missing to reach their desired SOC are charged first. The vehicles are charged
  one after the other.
- Balanced: Every vehicle is charged in such a way that it uses the entire cross section of its standing time and
  charging windows.

If not all vehicles can be charged during the time windows where charging is encouraged, the rest of the energy is
charged in non-charging windows. The remaining energy consumption of the entire fleet is balanced out across all
non-charging windows to keep power peaks as low as possible.

Distributed
-----------
This strategy uses different charging strategies at different grid connectors. A differentiation is made between depot
and opportunity charging stations. Vehicles connected to opportunity charging stations are charged according to the
'greedy' strategy. Vehicles connected to depot charging stations are charged according to the 'balanced' strategy. At
sites with a limited number of charging stations the vehicles are prioritized as follows: All vehicles that want to
connect in the current and future time steps are collected and ranked by their SOC. The vehicle(s) with lowest SOC are
loaded first until their desired SOC is reached or the vehicle departs. As soon as the charging station is available
again, the process is repeated.

.. image:: _files/example_strategies.png
   :width: 80 %

Incentive schemes
=================

The electricity costs for a location depend on the chosen charging strategy and incentive scheme. In
SpiceEV the current system for charging electricity (the state of the art) can be applied on all strategies. Any other
incentive scheme can only be applied on the corresponding charging strategy which is based on that incentive scheme.
The following table gives an overview of the possible combinations.

+--------------------------+-----------------------------+-------------------------------+-------------------------------+-------------------------------+
|**Charging strategy**     | **State of the art**        | **Time-variable grid fees**   |  **Flexible load windows**    | **Schedule-based grid fees**  |
+--------------------------+-----------------------------+-------------------------------+-------------------------------+-------------------------------+
| Greedy                   | x                           |                               |                               |                               |
+--------------------------+-----------------------------+-------------------------------+-------------------------------+-------------------------------+
| Balanced                 | x                           |                               |                               |                               |
+--------------------------+-----------------------------+-------------------------------+-------------------------------+-------------------------------+
| Balanced Market          | x                           | x                             |                               |                               |
+--------------------------+-----------------------------+-------------------------------+-------------------------------+-------------------------------+
| Schedule                 | x                           |                               |                               | x                             |
+--------------------------+-----------------------------+-------------------------------+-------------------------------+-------------------------------+
| Peak load window         | x                           |                               |  x                            |                               |
+--------------------------+-----------------------------+-------------------------------+-------------------------------+-------------------------------+
| Flex window              | x                           |                               |  x                            |                               |
+--------------------------+-----------------------------+-------------------------------+-------------------------------+-------------------------------+
| Distributed              | x                           |                               |                               |                               |
+--------------------------+-----------------------------+-------------------------------+-------------------------------+-------------------------------+

The electricity costs consist of the grid fees (sells included), taxes, levies and power
procurement. In case of V2G or feed-in by a PV power plant the feed-in remuneration is subtracted [#]_. The differences
between the incentive schemes lie in the way grid fees are handled. Therefore the other cost components are spared
out in the following explanations. In all of the incentive schemes the calculation of the grid fee is based on the price sheet of the
distribution grid operator.

In the following the current system for charging electricity as well as the three alternative incentive schemes are
explained. The alternative schemes differentiate between the fixed and flexible loads and bill them differently. Since
only flexible loads can respond to incentives time-variable grid fees, flexible time windows and schedule-based grid
fees are only applied on them. Fixed loads are charged according to the state of the art.

State of the art
----------------

Today a commodity charge is applied on the amount of electrical energy supplied from the grid. Additionally SLP
customers (standard load profile) have to pay a fixed basic charge per year. RLM customers (consumption metering) pay a
capacity charge instead which is multiplied with the maximum power supplied at the grid connector in one year. Depending
on the time of grid utilization one out of two different RLM tariffs for commodity and capacity charge are used. For a
grid utilization time >= 2500 h/a and therefore a low peak load compared to the amount of supplied energy per year, a
lower commodity charge and a higher capacity charge is given. This way grid friendly energy supply is rewarded.

Time-variable grid fees
-----------------------
For this incentive scheme a price time series with variable commodity charge is given which reflects the grid
situation. During times of low power flow or high renewable feed-in the prices are lower than in times of high power
flow due to grid supply. The price time series contains three tariff levels.

The supplied energy is multiplied with the commodity charge given during the time of supply. This way an incentive is
set for customers to charge their vehicles at times when the risk of an overload of the grid equipment is lower. In this
incentive model, it may happen that high power supply is encouraged in order to take excess electricity from renewable
power plants. Since the customers should not be financially worse off for this desired behavior by having to pay high
capacity related costs, only the peak demand in the times of the highest tariff is relevant for the capacity charge for
the flexible loads. Additionally, despite the actual utilization time of the power grid, the capacity charge for grid
friendly charging is used.

Flexible time windows
---------------------

Based on the forecast grid situation, low tariff windows and high tariff windows are defined. If curtailment of
renewable power plants is forecast or local generation outweighs load, these periods become low tariff windows.

When using flexible time windows the flexible loads such as electric vehicles are charged with the tariff for grid
friendly charging from the price sheet. Load peaks in low tariff time windows are not taken into account when
determining the capacity related costs. The calculation of the capacity related costs is based exclusively on the power
peaks in high-tariff windows. This way grid supply during times of curtailment of renewable power plants or high feed-in
is encouraged.

Schedule-based grid fees
------------------------

Similar to the flexible time windows, the tariff for grid friendly charging is applied on the flexible loads such as
electric vehicles when using schedule-based grid fees. However, a capacity charge is not applied on the flexible load.
Instead, the deviation of the total load from the schedule is charged by multiplying the maximum positive deviation
with a deviation charge. These capacity related costs are determined for grid supply. Deviations in feed-in are not
taken into account.



.. rubric:: Footnotes

.. [#] In the current version of SpiceEV the feed-in remuneration is only determined for photovoltaic power plants with
       a nominal power <= 100 kW.