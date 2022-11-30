.. _charging_strategies:

~~~~~~~~~~~~~~~~~~~
Charging strategies
~~~~~~~~~~~~~~~~~~~

Charging
========

The core of SpiceEV are the different charging strategies. They decide how to react to events, when to charge the cars and by how much. To see how to set strategy options, refer to [this wiki page](Command-line-options).

Stationary batteries supported: **YES**

V2G supported: **YES**

Greedy
------

Your most basic and dumb strategy. Charges one vehicle after the next with full power until the desired state of charge (SoC) is reached. Depending on the grid connector (GC), multiple cars may be charged in one timestep.

May charge above the desired SoC if there is surplus feed-in power or the energy price is cheap.

Balanced
--------

Each car is charged such that it uses its complete standing time to reach the desired SoC. May charge more power (and above the desired SoC) if there is surplus feed-in power or if the energy price falls below a certain PRICE_THRESHOLD.

Implementation notice: uses a binary search to find the minimum viable charging power.

GreedyMarket
------------
This algorithm first determines the cheapest group of time intervals sufficient to charge all vehicles according to their needs.
Moves all charging events to those time intervals and charges them with full power, similar to the greedy strategy. Only one grid connector supported.

BalancedMarket
--------------
When using this strategy, price information within the next *HORIZON* hours is evaluated. The goal is to divide standing times into periods of equal prices. A vehicle is now charged such that is uses the entire duration of all periods with the lowest price combined to reach its desired SOC. In case that time is not sufficient the periods of the second cheapest price are used to charge as much of the remaining delta SOC as possible, again in a balanced way with respect to power.

Schedule
--------
Allocate power according to grid operator schedule. Implements different sub-strategies:

- collective:
- individual:

PeakLoadWindow
--------------
Given a time window of high load, tries to charge outside this window. Different sub-strategies supported:

- greedy: vehicles charge as much as possible, one after the other (vehicles below desired SoC charge first)
- needy: power is allocated according to missing power needed to reach the desired SoC
- balanced: power is distributed evenly among vehicles below desired SoC. Surplus is then distributed evenly among all cars

V2G supported: **NO**

FlexWindow
----------
There are time windows during which charging is encouraged and there are those where it is discouraged. These time windows are determined by the grid operator (similar to Schedule strategy). During those windows where charging is encouraged the vehicles are charged with one of the following sub-strategies:

- greedy: charge vehicles that are below their desired SOC level one after the other, the rest is ordered by time of departure (earlier departures charged first)
- needy: charge vehicles with little power missing to desired SoC first, vehicles are charged one after the other
- balanced (DEFAULT): Go through vehicles one by one determining the amount of power for charging such that vehicle uses entire cross section of standing time and charging window

If not all vehicles can be charged during the time windows where charging is encouraged, the rest of the energy is charged in non-charging windows. The remaining energy consumption of the entire fleet is balanced out across all non-charging windows to keep power peaks as low as possible.

Distributed
-----------

Distributed is a strategy that supports multiple grid connectors. The ending of each charging station name indicates if it is a 'depot' or a 'opp' (opportunity charging) station. Vehicles connected to opp grid connectors are charged according to the 'greedy' strategy. Vehicles connected to depot grid connectors are charged according to the 'balanced' strategy. A maximum number of charging stations can be assigned for each grid connector ('number_cs').

Prioritization of vehicles at stations with a limited number charging stations:

If the number of charging stations is limited, all vehicles that want to connect in the current and future time steps (limited by C-HORIZON) are collected and ranked by their SoC. The vehicle(s) with lowest SoC are loaded first until their desired SoC is reached or the vehicle departs.
As soon as the charging station is available again, the process is repeated.

Incentive scheme
================
