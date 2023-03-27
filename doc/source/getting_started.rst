~~~~~~~~~~~~~~~
Getting started
~~~~~~~~~~~~~~~

SpiceEV is a tool to generate scenarios of electric vehicle fleets and simulate different charging strategies.

Installing SpiceEV
===================

You can clone the current repository of SpiceEV to your local machine using:

.. code:: bash

	git clone https://github.com/rl-institut/spice_ev

This tool just has an optional dependency on Matplotlib for plotting, an optional dependency on sphinx for the
documentation and an optional dependency on pytest for testing. Everything else uses the Python (>= 3.6) standard
library.

First steps
===========
Run SpiceEV
-----------

In order to run a simulation with SpiceEV you need to first generate your `scenario.json`, which then serves as the
input for your simulation. The respective script `generate.py` can be executed using several different generation modes.
It generates e.g. trips for certain vehicles by random functions or load your vehicle schedules from a CSV file. For a
better understanding of the modes the `generate` subscripts are documented :ref:`here <generate>`.
For now we use the `generate.py` script with the default mode "statistics" to create random trips for a number of
predefined vehicles. Vehicles should be defined in a `vehicle_type.json` and be added to your input arguments. See
`examples/data/vehicle_types.json` for some exemplary vehicles. You can define your input arguments in the command line
or open a configuration file (e.g. `examples/configs/generate.cfg`) to set your variables. For an overview over all
command line options see section :ref:`Command line options <command_line_options>`.

In order to generate a 7 day scenario with 10 vehicles of different types and timesteps of 15 minutes with command line
options, type:

.. code:: bash

    ./generate.py statistics --days 7 --vehicles 6 golf --vehicles 4 sprinter --interval 15 --vehicle-types examples/data/vehicle_types.json --output scenario.json

In order to generate a scenario with input arguments from a configuration file, type:

.. code:: bash

    ./generate.py --config ./examples/configs/generate.cfg


Now that you have created your first scenario, you can run a simulation. You need to define the path to the input
`scenario.json` and the charging strategy you want to use. In this case we use the greedy strategy and set `--visual` to
plot the results.

.. code:: bash

    ./simulate.py scenario.json --strategy greedy --visual

Again, you can alternatively define the input arguments in a configuration file, as in `examples/configs/simulate.cfg`:

.. code:: bash

    ./simulate.py --config ./examples/configs/simulate.cfg

Generate grid operator schedules
--------------------------------

If you want to generate a grid operator schedule from an input CSV file and include it to an existing `scenario.json`,
you can do this by running `generate_schedule.py`:

.. code:: bash

    ./generate_schedule.py scenario.json --input examples/data/grid_situation.csv --output examples/schedule.csv

In this case a CSV time series is read in from the folder `examples/data/` and the created schedule is saved in
`examples/`. The schedule CSV is automatically added to the `scenario.json`. Note that when running the
`generate_schedule.py` script, you need to already have an existing `scenario.json` that you want to add the schedule to.

Include other CSV time series
-----------------------------

You can also include your previously generated or already existing price time series, additional fixed load and/or local
generation time series to your input arguments when generating the `scenario.json`. More information on the file formats
of the input files can be found here: :ref:`Input and output file formats <file_formats>`.

.. code:: bash

    ./generate.py --include-price-csv ../price/price.csv --include-fixed-load-csv external_load.csv -o example.json


Help
----
In order to show all command line options type:

.. code:: bash

    ./generate.py -h
    ./simulate.py -h


As said above, there are also example configuration files in the example folder.

.. code:: bash

    ./generate.py --config examples/configs/generate.cfg examples/scenario.json
    ./simulate.py --config examples/configs/simulate.cfg examples/scenario.json



License
=======

MIT License

Copyright (c) 2022 Reiner Lemoine Institut

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
