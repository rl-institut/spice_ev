# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

SpiceEV (Simulation Program for Individual Charging Events of Electric Vehicles) simulates the
charging of electric vehicle fleets at one or more grid connectors under different charging
strategies. It produces time series of grid connector (GC), charging station (CS), vehicle and
stationary battery power / SoC and can calculate electricity costs afterwards. It is used
standalone and as the charging-simulation backend of [SimBA](https://github.com/rl-institut/SimBA)
(which calls `Scenario.run("distributed", ...)`).

## Setup

```bash
pip install -e .                 # no install_requires; core is pure standard library
pip install matplotlib           # optional, plots / --visual
pip install oemof.solph pandas   # optional, only for strategy oemof_solph (+ a solver, default cbc)
pip install flake8 darglint pytest
pip install -r doc/requirements.txt   # sphinx docs in doc/
```

## Running SpiceEV

The workflow is: generate a scenario JSON → simulate it → (optionally) calculate costs.

```bash
python generate.py --config examples/configs/generate.cfg        # modes: statistics, csv, simbev
python simulate.py --config examples/configs/simulate.cfg
python simulate.py examples/scenario.json --strategy greedy -so KEY VALUE --visual
python calculate_costs.py --config examples/configs/calculate_costs.cfg
python generate_schedule.py --config examples/configs/generate_schedule.cfg
```

Config files are `key = value` lines (values parsed as JSON, `#` comments), read by
`spice_ev.util.set_options_from_config`; config values override CLI arguments. Paths in configs
are relative to the repo root, CSV paths inside a scenario JSON relative to that JSON.
`-so/--strategy-option KEY VALUE` sets strategy attributes (see "Strategies" below).

## Tests & linting

```bash
python -m pytest tests/                                  # full suite
python -m pytest tests/test_strategies.py                # single file
python -m pytest tests/test_strategies.py::TestClass::test_x
flake8                                                   # max-line-length=100, darglint (sphinx)
```

CI (`.github/workflows/pythonpackage.yaml`) runs `flake8 .`, `pytest tests/` and the sphinx build
(`make -C doc html`) on Python 3.8 and 3.10 for every push. `darglint` checks that docstring
`:param:`/`:type:`/`:return:`/`:raises:` blocks match the signature. `tests/test_examples.py` runs
the example configs and compares against `examples/output/` — changing example configs or
`examples/scenario.json` changes test results. oemof is not installed in CI; the `oemof_solph` tests in
`tests/test_oemof_objective.py` skip without it.

## Git workflow (from CONTRIBUTING.md)

- Base branch is `dev`; `master` holds releases. Branches: `feature/<short-name>`,
  `fix/<short-name>`, `hotfix/...`, `release/vX.Y.Z`.
- Commit messages: short imperative subject line (< 50 chars), no trailing period, optionally the
  issue number.
- PRs target `dev`, include `Close #<issue>`, need a reviewer, and add an entry to `CHANGELOG.md`.

## Code style

- **Function granularity (YAGNI):** prefer fewer, larger functions over many small ones. Extract a
  helper only when it is called from more than one place or represents a clearly separate concern.
  A strategy's `step()` coordinating several stages in one method is the norm.
- **Docstrings:** Sphinx style (`""" Short summary.` with leading space), a one-line summary,
  occasionally one more sentence, then `:param:`/`:type:`/`:return:`/`:rtype:` (enforced by
  darglint). No design rationale or algorithm walkthroughs in docstrings — put those into
  `doc/source/` and short inline comments.
- **Inline comments:** liberal, short, lowercase, local — label what the next few lines do. Only
  exception: a short warning about a non-obvious pitfall right where it applies.
- Float comparisons use `self.EPS`. Tunable strategy constants are UPPERCASE attributes set
  *before* `super().__init__()` so that strategy options (kwargs) can override them.
- Use `warnings.warn` for recoverable problems; matplotlib / oemof are imported lazily or only in
  the modules needing them, the core stays dependency-free.

## No project data in public files

Real project data (e.g. SimBA project scenarios) lives outside this repo. No names or identifiers
from it may appear anywhere in this repo (code, tests, docs, example data, commit messages).

- Forbidden: grid operator names, station / stop / depot names, company or project names, project
  folder or file names, real rotation, line or vehicle IDs.
- Allowed: generic placeholders such as `GC1`, `CS_A`, depot `A`/`B`, vehicle type `sprinter`.
- When describing a problem found in project data, rewrite it with such placeholders.

## Architecture

- `spice_ev/scenario.py` — `Scenario(json_dict, dir_path)` reads `components`, `events` and
  `scenario` (`start_time`, `interval` in minutes, `stop_time` xor `n_intervals`).
  `Scenario.run(strategy_name, options)` injects `events`, `interval`, `stop_time`, `n_intervals`
  into the options, creates the strategy via `strategy.class_from_str`, then per time step:
  1. `Strategy.step(event_list)` of the **base class** processes due events (advances
     `current_time`, applies vehicle arrivals/departures, GC signals, fixed loads, generation);
  2. `strat.step()` of the concrete strategy returns `{"current_time", "commands": {cs_id: power}}`;
  3. results (GC loads, SoCs, prices, ...) are collected; afterwards `report.generate_reports`.
  An exception inside a strategy aborts the run but keeps results up to that point
  (`scenario.step_i < n_intervals` signals the abort, SimBA asserts on this).
- `spice_ev/components.py` — `Components` with `grid_connectors`, `charging_stations`,
  `vehicle_types`, `vehicles`, `batteries` (stationary), `photovoltaics`. `GridConnector.add_load`
  / `get_current_load`, `Vehicle.get_delta_soc` / `get_energy_needed`.
- `spice_ev/events.py` — `FixedLoad`, `LocalEnergyGeneration`, `GridOperatorSignal` (cost,
  max_power, target, window), `VehicleEvent` (`arrival` / `departure`, `update` dict with
  `soc_delta`, `desired_soc`, `connected_charging_station`, `estimated_time_of_departure`).
  `get_event_steps` buckets events by `signal_time`; strategies widen foresight by moving
  `signal_time` earlier (events are shared objects!).
- `spice_ev/strategy.py` — base `Strategy` (deep-copies components into `self.world_state`, sets
  defaults like `margin`, `ALLOW_NEGATIVE_SOC`, `EPS`, every kwarg becomes an attribute),
  event processing, `distribute_surplus_power`, `update_batteries`. `STRATEGIES` lists valid names;
  a new strategy needs a module in `spice_ev/strategies/`, a CamelCase class, an entry in
  `STRATEGIES` and in `costs.DEFAULT_COST_CALCULATION`.
- `spice_ev/battery.py`, `spice_ev/loading_curve.py` — analytic `load` / `unload` along a
  piecewise-linear charging curve (`target_soc`, `max_power`, `target_power`).
- `spice_ev/costs.py`, `calculate_costs.py` — price-sheet based cost calculation per GC.
- `spice_ev/report.py` — output CSV/JSON, plots, flexibility band.
- `spice_ev/generate/` — scenario generation (statistics, csv, simbev) and schedule generation.

### Strategies (`spice_ev/strategies/`)

- `greedy` — charge with maximum power immediately.
- `balanced` — minimum constant power that reaches `desired_soc` at departure.
- `balanced_market` — charge in the cheapest time steps within `HORIZON`.
- `peak_shaving` — flatten GC load (perfect foresight by default; `HORIZON` is a timedelta here).
- `peak_load_window` — avoid charging in grid operator time windows (`time_windows` file).
- `flex_window`, `schedule` — follow GC windows / a target schedule (`generate_schedule.py`).
- `distributed` — multi-GC strategy for bus depots and opportunity stations. GC type is derived
  from the CS ID suffix (`_deps` / `_opps`). It creates **one** instance each of `strategy_deps`
  and `strategy_opps` (with `strategy_options_deps` / `_opps` overriding the kwargs) and per time
  step, for every GC with connected vehicles (or a stationary battery), swaps a filtered
  `world_state` (that GC, its CS and vehicles by reference, filtered future events within the
  sub-strategy's `HORIZON`) into the sub-strategy and calls its `step()`. Consequently a
  sub-strategy's `step()` may run several times or not at all per time step, and only the
  `distributed` instance processes events. Optional `number_cs` per GC limits simultaneously
  charging vehicles (prioritised by SoC). Stationary batteries at opportunity stations are
  simulated as virtual vehicles.
- `oemof_solph` — builds one linear oemof.solph energy system over the whole simulation horizon in
  `__init__` from `kwargs["events"]` (GC sources/sinks with cost and max power, fixed loads,
  generation, stationary batteries, one storage + bus per vehicle, driving sink, CS links active
  only while connected), solves it once and stores the optimal SoC per battery and time step.
  `step()` then charges/discharges each battery to the precomputed SoC of the current time step
  (index derived from `current_time`, so it also works as `strategy_deps` of `distributed`).
  Details:
  - GCs coupled by vehicles visiting several of them form one energy system; independent groups
    are built and solved separately (keeps pyomo models small).
  - `desired_soc` at departure is a soft constraint (`DESIRED_SOC_PENALTY` per kWh shortfall);
    vehicles still connected at the end must reach it by their estimated departure / sim end.
  - Negative SoC is not possible in the model: if a vehicle uses more than a full battery between
    two charging connections, the strategy aborts with "oemof-solph does not allow for negative
    SoC, scenario not feasible". An infeasible LP (e.g. too little standing time / power between
    rotations) aborts with the same message.
  - `PEAK_POWER_COST` (€/kW·a, not annualized) per GC peak avoids arbitrary load peaks with
    constant prices. The desired SoC penalty per GC group is
    max(`DESIRED_SOC_PENALTY`, `DESIRED_SOC_PENALTY_FACTOR` · highest saving per kWh), the
    auxiliary flow cost 1e-4 · cheapest energy price (separate from the tolerance `EPS`).
  - Driving between two CS connections is spread evenly over the time steps in between;
    connections within the same time step as the last departure are treated as driving.
  - `step()` never discharges non-V2G vehicles and respects the GC limit if the actual SoC
    deviates from the plan. oemof refuses root logger level DEBUG, the strategy raises it to INFO
    temporarily. Solver via `SOLVER` (default `cbc`).
  - The objective comes from a mandatory `oemof_objective.json` (strategy option
    `oemof_objective`, read and validated in `spice_ev/oemof_objective.py`, per-GC settings via
    `gc_overrides`). Energy prices come from the cost parameter file (`cost_parameters_file` /
    SimBA `cost_parameters_path`), not from GC cost signals: net, annualized, weighted.
  - In `distributed`, sub-strategies only get the GCs of their station type, so oemof does not
    plan charging at opportunity stations (treated as driving).
