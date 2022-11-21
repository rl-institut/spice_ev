.. _file_formats:

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Input and output file formats
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

SpiceEV uses human-readable files for inputs, scenario definitions, configuration files and outputs. Not every type of input is part of the repository, as some data is classified and/or should be created by the user according to need.

generate.py / generate_from_simbev.py
=====================================

Inputs
------
**External load**

File type: CSV

Needs one column with the drawn energy in kWh (can have more columns, but only one is relevant). The file is read line-by-line, with events starting at start_time and updating every interval (configurable).

**Feed-in**

File type: CSV

Needs one column with the energy produced in kWh (can have more columns, but only one is relevant). The file is read line-by-line, with events starting at start_time and updating every interval (configurable).

**Energy price**

File type: CSV

Needs one column with the energy price in ct/kWh (can have more columns, but only one is relevant). The file is read line-by-line, with events starting at start_time and updating every interval (configurable). Can be created with generate_energy_price.py.

**Configuration**

File type: text

Refer to generate.cfg and generate_from_simbev.cfg in examples folder.

Output
------
File type: JSON

To be used in simulate.py. Defines general info (start_time, interval, n_intervals), constants (vehicle types, vehicles, grid connectors, charging stations, batteries) and events (external loads, feed-in, grid operator signals and vehicle events).

generate_from_csv.py
====================
Inputs
------
**Trips_schedule**

File type: CSV

Each row in csv file represents one trip. The following columns are needed:

departure time (datetime), arrival time (datetime), vehicle_type (str), soc (numeric) / delta_soc (numeric) / distance (numeric)
optional columns: vehicle_id (str)

**Configuration**

File type: text

Refer to generate_from_csv.cfg in examples folder or the generate_from_csv_template.csv

Output
------
File type: JSON

Scenario JSON

generate_energy_price.py
========================

Inputs
------

**Configuration**

File type: text

Refer to price.cfg in examples folder.

Output
------
File type: CSV

To be used in generate-scripts. Columns date, time and price [ct/kWh].


generate_schedule.py
========================

Inputs
------
** Grid operator schedule**

File type: csv

Needed columns: curtailment (numeric), residual load (numeric)

**Configuration**

File type: text

Refer to price.cfg in examples folder.

Output
------
File type: CSV

To be used in generate-scripts. Columns timestamp, schedule [kW], charge (0 or 1).

simulate.py
===========

Inputs
------
**Szenario (required)**

File type: JSON

Is created by generate.py or generate_from_simbev.py.

**Configuration**

File type: text

Refer to simulate.cfg in examples folder.

Output (optional)
------------------

File type: CSV

All power values are in kWh.

+-------------------------------------+---------------------------------------------------------------------------+
| **Column**                          | **Description**                                                           |
+-------------------------------------+---------------------------------------------------------------------------+
| timestep 	                      | simulation timestep, starting at 0                                        |
+-------------------------------------+---------------------------------------------------------------------------+
| time 	                              | datetime of timestep, isoformat                                           |
+-------------------------------------+---------------------------------------------------------------------------+
| grid power	                      | power drawn from grid                                                     |
+-------------------------------------+---------------------------------------------------------------------------+
| ext. loads	                      | sum of external loads, e.g. building power (omitted if not present)       |
+-------------------------------------+---------------------------------------------------------------------------+
| feed-in 	                      | sum of renewable energy sources feed-in power (omitted if not present)    |
+-------------------------------------+---------------------------------------------------------------------------+
| surplus 	                      | unused power from feed-in (omitted if no feed-in present)                 |
+-------------------------------------+---------------------------------------------------------------------------+
| sum CS power                        | total of power drawn by charging stations                                 |
+-------------------------------------+---------------------------------------------------------------------------+
| sum for each SimBEV use-case        | SimBEV only                                                               |
+-------------------------------------+---------------------------------------------------------------------------+
| # occupied CS                       |	number of charging stations with a car connected to it                    |
+-------------------------------------+---------------------------------------------------------------------------+
| #occupied for each SimBEV use-cases |	SimBEV only                                                               |
+-------------------------------------+---------------------------------------------------------------------------+
| CS name                             |	power at each charging station                                            |
+-------------------------------------+---------------------------------------------------------------------------+