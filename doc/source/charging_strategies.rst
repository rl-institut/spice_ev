.. _charging_strategies:

~~~~~~~~~~~~~~~~~~~
Charging strategies
~~~~~~~~~~~~~~~~~~~

Charging
========

The core of SpiceEV are the different charging strategies. They decide how to react to events, when to charge the vehicles and by how much. To see how to set strategy options, refer to [this wiki page](Command-line-options). The following table indicates whether a charging strategy considers stationary batteries and V2G.

+--------------------------+-----------------------------+-------------------------------+-------------------------------+
|**charging strategy**     | **stationary batteries**    | **V2G**                       |  **local feed-in**            |
+--------------------------+-----------------------------+-------------------------------+-------------------------------+
| greedy                   | x                           | x                             |  x                            |
+--------------------------+-----------------------------+-------------------------------+-------------------------------+
| balanced                 | x                           | x                             |  x                            |
+--------------------------+-----------------------------+-------------------------------+-------------------------------+
| greedy market            | x                           | x                             |  x                            |
+--------------------------+-----------------------------+-------------------------------+-------------------------------+
| balanced market          | x                           | x                             |  x                            |
+--------------------------+-----------------------------+-------------------------------+-------------------------------+
| schedule                 | x                           | x                             |  x                            |
+--------------------------+-----------------------------+-------------------------------+-------------------------------+
| peak load window         | x                           |                               |  x                            |
+--------------------------+-----------------------------+-------------------------------+-------------------------------+
| flex window              | x                           | x                             |  x                            |
+--------------------------+-----------------------------+-------------------------------+-------------------------------+
| distributed              | x                           | x                             |  x                            |
+--------------------------+-----------------------------+-------------------------------+-------------------------------+

Greedy
------
As soon as a vehicle is connected, it is charged at the maximum possible power until the desired SoC level is reached.
Depending on the grid connector (GC), the power must be throttled in order to not exceed its maximum power. A vehicle
may be charged above the desired SoC if there is surplus feed-in power or the energy price falls below the set price threshold.

Balanced
--------
Each vehicle is charged with the minimal possible power over its standing time to reach the desired SoC. A vehicle
may be charged above the desired SoC if there is surplus feed-in power or the energy price falls below the set price threshold.
A prerequisite for this strategy is an estimate of the standing time. In the simulation model, a perfect foresight is used for
this purpose. By defining a time horizon, it is possible to specify how far in the future departure times are known.

GreedyMarket
------------
Depending on a given price time series, this algorithm first determines the cheapest group of time intervals sufficient
to charge all vehicles according to their needs. All charging events are moved to those time intervals in which the
vehicles are charges with full power at the first cheapest price signal, similar to the greedy strategy.

BalancedMarket
--------------
Vehicles are oriented to an external price time series, which extends over a fixed time horizon. An attempt is made to
shift as much electricity consumption as possible to the times of low prices. A vehicle is charged such that is uses the
entire duration of all periods with the lowest price to reach the desired SOC, whereby the minimum possible charging
power of each vehicle type is also taken into account. In case that time is not sufficient the periods of the next
higher price are used for charging, too. The price time series can come, for example, from the electricity supplier or
distribution network operator via time-variable grid fees.

Schedule
--------
The distribution network operator sends an individual "charging schedule" to the connection users which contains the
time and amount of the total power to be called up for the grid connection. The charging schedule is based on the
flexibility potential of the connection users (total demand for electrical energy, maximum total power of the location
and the core standing time of the vehicle fleet (only for "collective") as well as the expected grid situation.
The core standing time is a fixed period of time during which all vehicles are guaranteed to be available.
Two different sub-strategies can be used:

- collective: All vehicles are controlled as a unit.
- individual: All vehicles are controlled individually.

PeakLoadWindow
--------------
Given a time window of high load, this strategy tries to charge outside this window. Different sub-strategies are
supported:

- greedy: vehicles charge as much as possible, one after the other (vehicles below desired SoC charge first)
- needy: power is allocated according to missing power needed to reach the desired SoC
- balanced: power is distributed evenly among vehicles below desired SoC. Surplus is then distributed evenly among all vehicles

FlexWindow
----------
This strategy uses time windows during which charging is encouraged and there are those where it is discouraged. These time windows are determined by the grid operator (similar to Schedule strategy). During those windows where charging is encouraged the vehicles are charged with one of the following sub-strategies:

- greedy: charge vehicles that are below their desired SOC level one after the other, the rest is ordered by time of departure (earlier departures charged first)
- needy: charge vehicles with little power missing to desired SoC first, vehicles are charged one after the other
- balanced: Go through vehicles one by one determining the amount of power for charging such that vehicle uses entire cross section of standing time and charging window

If not all vehicles can be charged during the time windows where charging is encouraged, the rest of the energy is charged in non-charging windows. The remaining energy consumption of the entire fleet is balanced out across all non-charging windows to keep power peaks as low as possible.

Distributed
-----------
Distributed is a strategy that uses different strategies at different grid connectors. A differentiation is made between depot or opportunity
charging stations. Vehicles connected to opportunity charging stations are charged according to the 'greedy' strategy. Vehicles
connected to depot charging stations are charged according to the 'balanced' strategy. At stations with a limited number
of charging stations the vehicles are prioritized as follows: All vehicles that want to connect in the current and
future time steps are collected and ranked by their SoC. The vehicle(s) with lowest SoC are loaded first until their
desired SoC is reached or the vehicle departs. As soon as the charging station is available again, the process is
repeated.

.. image:: _files/example_strategies.png
   :width: 80 %

Incentive scheme
================

The electricity costs for a location depend on the chosen charging strategy and incentive scheme. In
SpiceEV the current system for charging electricity (the state of the art) can be applied on all strategies. Any other
incentive scheme can only be applied on the corresponding charging strategy which is based on that incentive scheme.
The following table gives an overview of the possible combinations.

+--------------------------+-----------------------------+-------------------------------+-------------------------------+-------------------------------+
|**charging strategy**     | **State of the art**        | **Time-variable grid fees**   |  **Flexible load windows**    | **Schedule-based grid fees**  |
+--------------------------+-----------------------------+-------------------------------+-------------------------------+-------------------------------+
| Greedy                   | x                           |                               |                               |                               |
+--------------------------+-----------------------------+-------------------------------+-------------------------------+-------------------------------+
| Balanced                 | x                           |                               |                               |                               |
+--------------------------+-----------------------------+-------------------------------+-------------------------------+-------------------------------+
| Greedy Market            | x                           | x                             |                               |                               |
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

State of the art
----------------
The electricity costs consist of the grid fees (sells included), taxes, levies and power
procurement. In case of V2G or feed-in by a PV power plant the feed-in remuneration is subtracted. The difference
between the incentive schemes lies in the the way grid fees are handled. Therefore the other cost components are spared
out in the following. In all of the incentive schemes the calculation of the grid fee is based on the price sheet of the
distribution grid operator.

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
incentive model, it may happen that high power supply is encouraged in order to take excess electricity. Since the
customers should not be financially worse off for this desired behavior by having to pay high capacity related costs,
only the peak demand in the times of the highest tariff is relevant for the capacity charge for the flexible loads.
Additionally, despite the actual utilization time of the power grid, the capacity charge for grid friendly charging is
used.

The time variable grid fees are only applied on flexible loads such as electric vehicles. The fixed load of a location
is charged according to the state of art.

Flexible time windows
---------------------

Based on the forecast grid situation, low tariff windows and high tariff windows are defined. If curtailment is
forecast or feed-in outweighs load, these periods become low tariff windows.

When using flexible time windows the flexible loads such as electric vehicles are charged with the tariff for grid
friendly charging from the price sheet. Load peaks in low tariff time windows are not taken into account when
determining the capacity related costs. The calculation of the capacity related costs is based exclusively on the power
peaks in high-tariff windows. This way grid supply during times of curtailment or high feed-in is encouraged.

The flexible time windows are only applied on flexible loads. The fixed load of a location is charged according to the
state of art.

Schedule-based grid fees
------------------------

Similar to the flexible time windows, the tariff for grid friendly charging is applied on the flexible loads such as
electric vehicles when using schedule-based grid fees. In case off a core standing time, only the load peak outside the
core standing time is relevant for the capacity charge, since this grid supply was not scheduled by the grid operator.

The schedule-based grid fees are only applied on flexible loads. The fixed load of a location is charged according to
the state of art.
