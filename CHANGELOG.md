# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Template:
```
## [0.0.0] - Name of Release - 20YY-MM-DD

### Added
- [(#)]()
### Changed
- [(#)]()
### Removed
- [(#)]()
```

## [1.1.0] - Update - 2024-02-11

### Added
- grid connector component: grid operator and voltage level information
- photovoltaics component
- time window strategies: peak shaving and peak load window
- command line option `skip_flex_report`  to skip flex band generation in report
### Changed
- discharge limit moved from general scenario setup to vehicle types
- made battery calculation more robust for large capacities (no more endless loops)
- discoupled cost calculation from strategy (allow different pricing schemes)
- rework of greedy, balanced, balanced_market, distributed, flex_window and schedule strategies

## [1.0.2] - Unreleased - 2023-10-17

### Changed
- distributed strategy: charge vehicles with any strategy at depot and opportunity stations
- distributed strategy: battery behavior adapted
- new strategies: peak_load_window
- plotting time windows with shaded backgrounds

## [1.0.1] - Minor Fixes - 2023-08-04

### Changed
- fixed bug if scenario has no timesteps
- fixed bug regarding V2G discharge power computation in balanced_market
- give summary of adjusted event times instead of warning for every affected event
- flake8 compatibility

## [1.0.0] - Initial Release SpiceEV - 2023-05-24

### Added
- first release! 🎉
