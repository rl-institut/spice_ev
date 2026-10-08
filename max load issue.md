# GC power limit check: reduced power limits feed-in, non-generation feed-in is silently clipped

## Description

The grid connector (GC) power check in `Scenario.run` (`spice_ev/scenario.py`) has two issues
concerning feed-in (negative GC load):

### 1. Reduced GC power (e.g. peak load windows) also limits feed-in

During a peak load window, a `GridOperatorSignal` reduces `gc.cur_max_power` (e.g. from 1310 kW
to 100 kW). The safety check compares the GC load symmetrically against this reduced value:

```python
powerLimit = gc.cur_max_power + strat.EPS
gcWithinPowerLimit = -powerLimit <= gc_load <= powerLimit
```

The reduced power is meant to limit **load** only. Feeding in more than the reduced power (e.g.
PV surplus at noon) is allowed, as long as the general GC power (`gc.max_power`) is not exceeded.
Currently, the simulation aborts in this case, e.g. with strategy `balanced` and local generation:

```
AssertionError: A maximum load exceeded: -112.3110202 / 100.0
```

### 2. All feed-in is clipped to the GC power, not only local generation

Right before the check, the GC load is clipped to the general GC power:

```python
gc_load = gc.get_current_load(exclude=local_generation_keys)
gc_load = max(-gc.max_power, gc_load - curLocalGeneration)
```

The intention is to curtail local generation. However, the clipping also applies to all other
feed-in (stationary batteries, V2G). If e.g. a battery feeds in more than `gc.max_power`, the
energy is removed from the battery but not accounted for in the GC load, and no error is raised.
As a consequence, the lower bound of the safety check can never fail.

## Proposed solution

- Only curtail local generation; leave other feed-in unchanged, so it is caught by the check:
  ```python
  gc_load = max(min(-gc.max_power, gc_load), gc_load - curLocalGeneration)
  ```
- Check load against the current (possibly reduced) GC power and feed-in against the general
  GC power:
  ```python
  gcWithinPowerLimit = (
      -gc.max_power - strat.EPS <= gc_load <= gc.cur_max_power + strat.EPS)
  ```

This guarantees for every completed simulation:

- load never exceeds `gc.cur_max_power` (and therefore never `gc.max_power`, since
  `cur_max_power = min(max_power, signal)`),
- feed-in never exceeds `gc.max_power` (local generation is curtailed, other feed-in raises an
  error).

Implemented on branch `fix/gc_feed_in_limit`.

## Notes

- Found with SimBA (strategy `balanced`, stationary batteries, PV via `energy_feed_in`, peak load
  windows).
- `tests/test_generate.py::TestGenerate::test_generate_with_batteries` already fails on `dev`
  without this change (unrelated).
