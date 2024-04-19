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

## [1.0.2] - Unreleased - 2023-10-17

### Changed
- distributed strategy: charge vehicles with any strategy at depot and opportunity stations
- distributed strategy: battery behavior adapted
- new strategies: peak_load_window
- plotting time windows with shaded backgrounds
- changed total power plot: remove individual loads, add sum of batteries, add GC load

## [1.0.1] - Minor Fixes - 2023-08-04

### Changed
- fixed bug if scenario has no timesteps
- fixed bug regarding V2G discharge power computation in balanced_market
- give summary of adjusted event times instead of warning for every affected event
- flake8 compatibility

## [1.0.0] - Initial Release SpiceEV - 2023-05-24

### Added
- first release! 🎉
