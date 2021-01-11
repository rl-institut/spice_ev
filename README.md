# Netz\_eLOG Tool

A tool to generate scenarios of electric-vehicle fleets and simulate different charging
strategies.

## Installation

Just clone this repository. This tool just has an optional dependency on
Matplotlib. Everything else uses the Python (>= 3.6) standard library.

## Examples

Generate a scenario and store it in a JSON file:

```sh
./generate.py example.json
```

Run a simulation of this scenario using the `greedy` charging strategy and show
plots of the results:

```sh
./simulate.py kleinmachnow.json --strategy greedy --visual
```

Show all command line options:

```sh
./generate -h
./simulate.py -h
```
