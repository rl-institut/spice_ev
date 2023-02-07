import datetime
import json
from pathlib import Path

from spice_ev import util


def aggregate_global_results(scenario):
    """ Aggregate and reorder simulation data across grid connectors.
        Quantities:
        Total load per timestep across grid connectors,
        Load per charging station and time,
        All loads per grid connector except for charging stations

    :param scenario: Scenario for which to aggregate data.
    :type scenario: spice_ev.Scenario
    """
    gc_ids = scenario.components.grid_connectors.keys()
    all_totalLoad = [sum(x) for x in zip(*scenario.totalLoad.values())]

    sum_cs = []
    for r in scenario.results:
        cur_cs = []
        for cs_id in sorted(scenario.components.charging_stations):
            cur_cs.append(r['commands'].get(cs_id, 0.0))
        sum_cs.append(cur_cs)

    # untangle external loads (with feed-in)
    loads = {}
    for gcID in gc_ids:
        loads[gcID] = {}
        for i, step in enumerate(scenario.extLoads[gcID]):
            for k, v in step.items():
                if k not in loads[gcID]:
                    # new key, not present before
                    loads[gcID][k] = [0] * i
                loads[gcID][k].append(v)
            for k in loads[gcID].keys():
                if k not in step:
                    # old key not in current step
                    loads[gcID][k].append(0)

    scenario.loads = loads
    scenario.sum_cs = sum_cs
    scenario.all_totalLoad = all_totalLoad


def aggregate_local_results(scenario, gcID):
    """ Aggregate results of simulation for a single grid connector.
        Aggregated Quantities:
        avg flex per window,
        sum of energy
        sum of energy per window,
        avg standing time,
        standing per window,
        avg needed energy,
        power peaks,
        average drawn power,
        feed-in energy,
        max. stored energy in batteries,
        stationary battery cycles,
        all vehicle battery cycles

    :param scenario: Scenario for which to aggregate results.
    :type scenario: spice_ev.Scenario
    :param gcID: Grid connector to aggregate results for.
    :type gcID: str
    :return: Aggregated results
    :rtype: dict
    """
    json_results = {}
    steps = scenario.step_i
    stepsPerHour = scenario.stepsPerHour

    if gcID not in scenario.flex_bands.keys():
        if 'generate_flex_band' not in locals().keys():
            # cyclic dependency: import when needed
            from spice_ev.generate.generate_schedule import generate_flex_band
        try:
            scenario.flex_bands[gcID] = generate_flex_band(scenario, gcID)
        except Exception:
            scenario.flex_bands[gcID] = {}

    json_results["temporal_parameters"] = {
        "interval": scenario.interval.total_seconds() // 60,
        "unit": 'minutes',
        "info": "simulation interval"
    }

    if scenario.core_standing_time:
        json_results["core_standing_time"] = {
            "times": scenario.core_standing_time['times'],
            "no_drive_days": scenario.core_standing_time['no_drive_days'],
            "unit": "h",
            "info": "Core standing time: start time, end time, duration"
        }

    json_results["grid_connector"] = {
        "gcID": gcID,
        "voltage_level": scenario.components.grid_connectors[gcID].voltage_level
        }

    pvs = scenario.components.photovoltaics.values()
    nominal_pv_power = sum([pv.nominal_power for pv in pvs if pv.parent == gcID])

    json_results["photovoltaics"] = {
        "nominal_power": nominal_pv_power,
        "unit": "kWp",
        "info": "Nominal power of PV power plants"
    }

    json_results["charging_strategy"] = {
        "strategy": scenario.strategy_name,
        "info": "charging strategy for electric vehicles"
    }

    # gather info about standing and power in specific time windows
    load_count = [[0] for _ in scenario.components.vehicles]
    load_window = [[] for _ in range(4)]
    count_window = [[0]*len(scenario.components.vehicles) for _ in range(4)]

    cur_time = scenario.start_time - scenario.interval
    # maximum power (fixed and variable loads)
    max_fixed_load = 0
    max_variable_load = 0
    for idx in range(scenario.step_i):
        cur_time += scenario.interval
        time_since_midnight = cur_time - cur_time.replace(hour=0, minute=0)
        # four equally large time_windows: 04-10, 10-16, 16-22, 22-04
        # shift time by four hours
        shifted_time = time_since_midnight - datetime.timedelta(hours=4)
        # compute window index
        widx = (shifted_time // datetime.timedelta(hours=6)) % 4

        flex = scenario.flex_bands[gcID]
        try:
            cur_load_window = (flex["max"][idx] - flex["min"][idx], scenario.totalLoad[gcID][idx])
        except KeyError:
            cur_load_window = (0, scenario.totalLoad[gcID][idx])
        load_window[widx].append(cur_load_window)

        count_window[widx] = list(map(
            lambda c, t: c + (t is not None),
            count_window[widx], scenario.socs[idx]))

        for i, soc in enumerate(scenario.socs[idx]):
            if soc is None and load_count[i][-1] > 0:
                load_count[i].append(0)
            else:
                load_count[i][-1] += (soc is not None)

        fixed_load = sum([v for k, v in scenario.extLoads[gcID][idx].items() if
                          k in scenario.events.external_load_lists or
                          k in scenario.events.energy_feed_in_lists])
        max_fixed_load = max(max_fixed_load, fixed_load)
        var_load = scenario.totalLoad[gcID][idx] - fixed_load
        max_variable_load = max(max_variable_load, var_load)

    # avg flex per window
    scenario.avg_flex_per_window[gcID] = [sum([t[0] for t in w]) / len(w) if w else 0
                                          for w in load_window]
    json_results["avg flex per window"] = {
        "04-10": scenario.avg_flex_per_window[gcID][0],
        "10-16": scenario.avg_flex_per_window[gcID][1],
        "16-22": scenario.avg_flex_per_window[gcID][2],
        "22-04": scenario.avg_flex_per_window[gcID][3],
        "unit": "kW",
        "info": "Average flexible power range per time window"
    }

    # sum of used energy during simulation
    json_results["sum of energy"] = {
        "value": sum(scenario.totalLoad[gcID]) / stepsPerHour,
        "unit": "kWh",
        "info": "Total drawn energy from grid connection point during simulation"
    }

    # sum of used energy per window
    scenario.sum_energy_per_window[gcID] = [sum([t[1] for t in w]) / stepsPerHour
                                            for w in load_window]
    json_results["sum of energy per window"] = {
        "04-10": scenario.sum_energy_per_window[gcID][0],
        "10-16": scenario.sum_energy_per_window[gcID][1],
        "16-22": scenario.sum_energy_per_window[gcID][2],
        "22-04": scenario.sum_energy_per_window[gcID][3],
        "unit": "kWh",
        "info": "Total drawn energy per time window"
    }

    # avg standing time
    # don't use info from flex band, as standing times might be interleaved
    # remove last empty standing count
    for counts in load_count:
        if counts[-1] == 0:
            counts = counts[:-1]
    num_loads = sum(map(len, load_count))
    avg_stand_time = scenario.avg_stand_time
    if num_loads > 0:
        avg_stand_time[gcID] = sum(map(sum, load_count)) / stepsPerHour / num_loads
    else:
        avg_stand_time[gcID] = 0
    # avg total standing time
    # count per vehicle: list(zip(*count_window))
    total_standing = sum(map(sum, count_window))
    # avoid div0 if there are no vehicles
    num_vehicles = max(len(scenario.components.vehicles), 1)
    scenario.avg_total_standing_time[gcID] = total_standing / num_vehicles / stepsPerHour
    json_results["avg standing time"] = {
        "single": avg_stand_time[gcID],
        "total": scenario.avg_total_standing_time,
        "unit": "h",
        "info": "Average duration of a single standing event and "
                "average total time standing time of all vehicles"
    }

    # percent of standing time in time window
    scenario.perc_stand_window[gcID] = list(map(
        lambda x: x * 100 / total_standing if total_standing > 0 else 0,
        map(sum, count_window)))
    json_results["standing per window"] = {
        "04-10": scenario.perc_stand_window[gcID][0],
        "10-16": scenario.perc_stand_window[gcID][1],
        "16-22": scenario.perc_stand_window[gcID][2],
        "22-04": scenario.perc_stand_window[gcID][3],
        "unit": "%",
        "info": "Share of standing time per time window"
    }

    # avg needed energy per standing period
    intervals = scenario.flex_bands[gcID].get("intervals")
    if intervals:
        scenario.avg_needed_energy[gcID] = sum([i["needed"] / i["num_vehicles_present"]
                                                for i in intervals]) / len(intervals)
    else:
        scenario.avg_needed_energy[gcID] = 0
    json_results["avg needed energy"] = {
        # avg energy per standing period and vehicle
        "value": scenario.avg_needed_energy[gcID],
        "unit": "kWh",
        "info": "Average amount of energy needed to reach the desired SoC"
                " (averaged over all vehicles and charge events)"
    }

    # power peaks (fixed loads and variable loads)
    if any(scenario.totalLoad[gcID]):
        json_results["power peaks"] = {
            "fixed": max_fixed_load,
            "variable": max_variable_load,
            "total": max(scenario.totalLoad[gcID]),
            "unit": "kW",
            "info": "Maximum drawn power, by fixed loads (building, PV),"
                    " variable loads (charging stations, stationary batteries) "
                    "and all loads"
        }

    # average drawn power
    scenario.avg_drawn[gcID] = sum(scenario.totalLoad[gcID]) / steps if steps > 0 else 0
    json_results["avg drawn power"] = {
        "value": scenario.avg_drawn[gcID],
        "unit": "kW",
        "info": "Drawn power, averaged over all time steps"
    }

    # total feed-in energy
    json_results["feed-in energy"] = {
        "value": sum(scenario.feedInPower[gcID]) / stepsPerHour,
        "unit": "kWh",
        "info": "Total energy from renewable energy sources"
    }

    # battery sizes
    for b in scenario.batteryLevels.values():
        if any(b):
            bat_dict = {batName: max(values) for batName, values in
                        scenario.batteryLevels.items()}
            bat_dict.update({
                "unit": "kWh",
                "info": "Maximum stored energy in each battery by name"
            })
            json_results["max. stored energy in batteries"] = bat_dict

    # charging cycles
    # stationary batteries
    total_bat_cap = 0
    for batID, battery in scenario.components.batteries.items():
        if scenario.components.batteries[batID].parent == gcID:
            if battery.capacity > 2**63:
                # unlimited capacity
                max_cap = max(scenario.batteryLevels[batID])
                print("Battery {} is unlimited, set capacity to {} kWh".format(
                    batID, max_cap))
                total_bat_cap += max_cap
            else:
                total_bat_cap += battery.capacity
    if total_bat_cap:
        total_bat_energy = 0
        for loads in scenario.extLoads[gcID]:
            for batID in scenario.components.batteries.keys():
                if scenario.components.batteries[batID].parent == gcID:
                    total_bat_energy += max(loads.get(batID, 0), 0) / stepsPerHour
        json_results["stationary battery cycles"] = {
            "value": total_bat_energy / total_bat_cap,
            "unit": None,
            "info": "Number of load cycles of stationary batteries (averaged)"
        }
    # vehicles
    vehicle_cap = sum([v.battery.capacity for v in scenario.components.vehicles.values()])
    vehicle_energy = sum([sum(map(lambda v: max(v, 0), r["commands"].values()))
                          for r in scenario.results])
    scenario.total_vehicle_cap[gcID] = vehicle_cap
    scenario.total_vehicle_energy[gcID] = vehicle_energy
    battery_cycles = vehicle_energy / vehicle_cap if vehicle_cap > 0 else 0
    json_results["all vehicle battery cycles"] = {
        "value": battery_cycles,
        "unit": None,
        "info": "Number of load cycles per vehicle (averaged)"
    }

    json_results["times below desired soc"] = {
        "without margin": scenario.strat.desired_counter,
        "with margin": scenario.strat.margin_counter,
        "margin": scenario.strat.margin,
        "info": "Number of times vehicle SoC was below desired SoC on departure "
                "(with and without margin of {}%)".format(scenario.strat.margin * 100)
    }

    return json_results


def aggregate_timeseries(scenario, gcID):
    """ Compute various timeseries for a given grid connector.

    The time series generated are:
        price [EUR/kWh],
        grid power [kW],
        ext.load [kW],
        feed-in [kW],
        flex min [kW],
        flex base [kW],
        flex max [kW],
        sum CS power [kW],
        number of occupied CS,
        power at CS (one per CS) [kW]

    The given scenario gains multiple list attributes used for calculating costs.

    :param scenario: Scenario for with to generate timeseries.
    :type scenario: spice_ev.Scenario
    :param gcID: ID of GC for which to generate timeseries.
    :type gcID: str
    :return: header and timeseries
    :rtype: dict
    """

    cs_ids = sorted(item for item in scenario.components.charging_stations.keys() if
                    scenario.components.charging_stations[item].parent == gcID)

    uc_keys = [
        "work",
        "business",
        "school",
        "shopping",
        "private/ridesharing",
        "leisure",
        "home",
        "hub"
    ]

    # round power and energy values to Watt and Watthours
    round_to_places = 3

    # which SimBEV-Use Cases are in this scenario?
    # group CS by UC name
    cs_by_uc = {}
    for uc_key in uc_keys:
        for cs_id in cs_ids:
            if uc_key in cs_id:
                # CS part of UC
                if uc_key not in cs_by_uc:
                    # first CS of this UC
                    cs_by_uc[uc_key] = []
                cs_by_uc[uc_key].append(cs_id)

    uc_keys_present = cs_by_uc.keys()

    # flex
    if not hasattr(scenario, "flex_bands"):
        setattr(scenario, "flex_bands", {})
    if gcID not in scenario.flex_bands.keys():
        if 'generate_flex_band' not in locals().keys():
            # cyclic dependency: import when needed
            from spice_ev.generate.generate_schedule import generate_flex_band
        try:
            scenario.flex_bands[gcID] = generate_flex_band(scenario, gcID)
        except Exception:
            scenario.flex_bands[gcID] = {}

    # any loads except CS present?
    hasExtLoads = any(scenario.extLoads)
    hasSchedule = any(s is not None for s in scenario.gcPowerSchedule[gcID])
    hasBatteries = sum([b.parent == gcID for b in scenario.components.batteries.values()])

    # accumulate header
    # general info
    header = ["timestep", "time"]
    # price
    if any(scenario.prices[gcID]):
        header.append("price [EUR/kWh]")
    # grid power
    header.append("grid power [kW]")
    # external loads
    if hasExtLoads:
        # external loads (e.g., building)
        header.append("ext.load [kW]")
    # feed-in
    if any(scenario.feedInPower):
        header.append("feed-in [kW]")
    # batteries
    if hasBatteries:
        header += ["battery power [kW]", "bat. stored energy [kWh]"]
    # flex
    header += ["flex min [kW]", "flex base [kW]", "flex max [kW]"]
    # schedule & window
    if hasSchedule:
        # external loads (e.g., building)
        header += ["schedule [kW]", "window"]
    # sum of charging power
    header.append("sum CS power")
    # charging power per use case
    header += ["sum UC {}".format(uc) for uc in uc_keys_present]
    # total number of occupied charging stations
    header.append("# occupied CS")
    # number of CS in use (delivering power)
    header.append("CS in use")
    # number of occupied CS per UC
    header += ["# occupied UC {}".format(uc) for uc in uc_keys_present]
    # charging power per CS
    header += [str(cs_id) for cs_id in cs_ids]

    # accumulate timesteps
    timeseries = []
    for idx, r in enumerate(scenario.results):
        # general info: timestep index and timestamp
        # TZ removed for spreadsheet software
        row = [idx, r['current_time'].replace(tzinfo=None)]
        # price
        if any(scenario.prices[gcID]):
            row.append(scenario.prices[gcID][idx])
        # grid power (negative since grid power is fed into system)
        row.append(-1 * round(scenario.totalLoad[gcID][idx], round_to_places))
        # external loads
        if hasExtLoads:
            sumExtLoads = sum([
                v for k, v in scenario.extLoads[gcID][idx].items()
                if k in scenario.events.external_load_lists])
            row.append(round(sumExtLoads, round_to_places))
        # feed-in (negative since grid power is fed into system)
        if any(scenario.feedInPower):
            row.append(-1 * round(scenario.feedInPower[gcID][idx], round_to_places))

        # batteries
        if hasBatteries:
            current_battery = {}
            for batID in scenario.batteryLevels:
                if scenario.components.batteries[batID].parent == gcID:
                    current_battery.update({batID: scenario.batteryLevels[batID]})
            row += [
                # battery power
                round(sum([
                    v for k, v in scenario.extLoads[gcID][idx].items()
                    if k in scenario.components.batteries]),
                    round_to_places),
                # battery levels
                # get connected battery
                round(
                    sum([levels[idx] for levels in current_battery.values()]),
                    round_to_places
                )
            ]
        try:
            # flex, might not exist
            row += [
                round(scenario.flex_bands[gcID]["min"][idx], round_to_places),
                round(scenario.flex_bands[gcID]["base"][idx], round_to_places),
                round(scenario.flex_bands[gcID]["max"][idx], round_to_places),
            ]
        except KeyError:
            row += [0, 0, 0]

        # schedule + window schedule
        if hasSchedule:
            row += [
                round(scenario.gcPowerSchedule[gcID][idx], round_to_places),  # float
                int(scenario.gcWindowSchedule[gcID][idx]),  # bool -> int
            ]

        # charging power
        # get sum of all current CS power that are connected to gc
        gc_commands = {}
        if r['commands']:
            for k, v in r["commands"].items():
                if k in cs_ids:
                    gc_commands.update({k: v})
        row.append(round(sum(gc_commands.values()), round_to_places))
        # sum up all charging power at gc for each use case
        row += [round(sum([cs_value for cs_id, cs_value in gc_commands.items()
                           if cs_id in cs_by_uc[uc_key]]),
                round_to_places) for uc_key in uc_keys_present]
        # get total number of occupied CS that are connected to gc
        row.append(len(scenario.connChargeByTS[gcID][idx]))
        # get number of CS that actually deliver power
        row.append(sum(map(bool, scenario.connChargeByTS[gcID][idx].values())))
        # get number of occupied CS at gc for each use case
        row += [
            sum([1 if uc_key in cs_id else 0
                for cs_id in scenario.connChargeByTS[gcID][idx]]) for uc_key in
            uc_keys_present]
        # get individual charging power of cs_id that is connected to gc
        row += [round(gc_commands.get(cs_id, 0), round_to_places) for cs_id in
                cs_ids]
        timeseries.append(row)

    # update scenario with timeseries data
    setattr(scenario, f"{gcID}_timeseries", dict(zip(header, map(list, zip(*timeseries)))))

    return {
        "header": header,
        "timeseries": timeseries,
    }


def generate_soc_timeseries(scenario):
    """ Generates an SOC timeseries for each vehicle.

    :param scenario: The scenario for which to generate SOC timeseries.
    :type scenario: spice_ev.Scenario
    """
    vids = sorted(scenario.components.vehicles.keys())
    scenario.vehicle_socs = {vid: [] for vid in vids}
    for ts_idx, socs in enumerate(scenario.socs):
        for vidx, vid in enumerate(vids):
            # combine SOCs from connected and disconnected timesteps
            # for every time step and vehicle, exactly one of the two has
            # a numeric value while the other contains a NoneType
            # (except if not known, like absent at beginning or end)
            soc = socs[vidx] or scenario.disconnect[ts_idx][vidx]
            scenario.vehicle_socs[vid].append(soc)


def plot(scenario):
    """ Plot various timeseries collected over the duration of the simulation.
        1. SOC over time per vehicle
        2. Power over time per charging station
        3. Power over time aggregated over all instances of various power sources
           and sinks, namely grid connectors, charging stations, PV, batteries
        4. Price over time per grid connector

    :param scenario: The scenario for which to generate the plots.
    :type scenario: spice_ev.Scenario
    """
    import matplotlib.pyplot as plt

    print('Done. Create plots...')

    xlabels = []
    for r in scenario.results:
        xlabels.append(r['current_time'])

    # batteries
    if scenario.batteryLevels:
        plots_top_row = 3
        ax = plt.subplot(2, plots_top_row, 3)
        ax.set_title('Batteries')
        ax.set(ylabel='Stored power in kWh')
        for name, values in scenario.batteryLevels.items():
            ax.plot(xlabels, values, label=name)
        ax.legend()
    else:
        plots_top_row = 2

    # vehicles
    ax = plt.subplot(2, plots_top_row, 1)
    ax.set_title('Vehicles')
    ax.set(ylabel='SoC')
    lines = ax.plot(xlabels, scenario.socs)
    # reset color cycle, so lines have same color
    ax.set_prop_cycle(None)

    ax.plot(xlabels, scenario.disconnect, '--')
    if len(scenario.components.vehicles) <= 10:
        ax.legend(lines, sorted(scenario.components.vehicles.keys()))

    # charging stations
    ax = plt.subplot(2, plots_top_row, 2)
    ax.set_title('Charging Stations')
    ax.set(ylabel='Power in kW')
    lines = ax.step(xlabels, scenario.sum_cs, where='post')
    if len(scenario.components.charging_stations) <= 10:
        ax.legend(lines, sorted(scenario.components.charging_stations.keys()))

    # total power
    ax = plt.subplot(2, 2, 3)
    ax.step(xlabels, list([sum(cs) for cs in scenario.sum_cs]), label="CS", where='post')
    gc_ids = scenario.components.grid_connectors.keys()
    for gcID in gc_ids:
        for name, values in scenario.loads[gcID].items():
            ax.step(xlabels, values, label=name, where='post')
    # draw schedule
    if scenario.strat.uses_window:
        for gcID, schedule in scenario.gcWindowSchedule.items():
            if all(s is not None for s in schedule):
                # schedule exists
                window_values = [v * int(max(scenario.totalLoad[gcID])) for v in schedule]
                ax.step(xlabels, window_values, label="window {}".format(gcID),
                        linestyle='--', where='post')
    if scenario.strat.uses_schedule:
        for gcID, schedule in scenario.gcPowerSchedule.items():
            if any(s is not None for s in schedule):
                ax.step(xlabels, schedule, label="Schedule {}".format(gcID), where='post')

    ax.step(xlabels, scenario.all_totalLoad, label="Total", where='post')
    ax.set_title('Power')
    ax.set(ylabel='Power in kW')
    ax.legend()
    ax.xaxis_date()  # xaxis are datetime objects

    # price
    ax = plt.subplot(2, 2, 4)
    prices = list(zip(*scenario.prices.values()))
    lines = ax.step(xlabels, prices, where='post')
    ax.set_title('Price for 1 kWh')
    ax.set(ylabel='€')
    if len(gc_ids) <= 10:
        ax.legend(lines, sorted(gc_ids))

    # figure title
    fig = plt.gcf()
    fig.suptitle('Strategy: {}'.format(scenario.strat.description), fontweight='bold')

    # fig.autofmt_xdate()  # rotate xaxis labels (dates) to fit
    # autofmt removes some axis labels, so rotate by hand:
    for ax in fig.get_axes():
        ax.set_xlim(scenario.start_time, scenario.stop_time)
        plt.setp(ax.get_xticklabels(), rotation=30, ha='right')

    plt.show()


def generate_reports(scenario, options):
    """
    Generate reports and save them.

    :param scenario: scenario to create reports for
    :type scenario: Scenario
    :param options: command line options
    :type options: dict
    """
    attach_vehicle_soc = options.get("attach_vehicle_soc")
    cost_calculation = options.get("cost_calculation")
    save_timeseries = options.get("save_timeseries")
    save_results = options.get("save_results")
    save_soc = options.get("save_soc")
    testing = options.get("testing")
    visual = options.get("visual")

    if save_results or testing:
        # initialize aggregation variables with empty dicts
        for var in ["avg_drawn", "flex_bands", "total_vehicle_cap", "avg_stand_time",
                    "total_vehicle_energy", "avg_needed_energy", "perc_stand_window",
                    "avg_flex_per_window", "sum_energy_per_window", "avg_total_standing_time"]:
            setattr(scenario, var, {})

    # check file extensions
    if save_results and Path(save_results).suffix != ".json":
        # general results should be JSON
        print("File extension mismatch: results file should be of type .json")
    if save_soc and Path(save_soc).suffix != ".csv":
        # vehicle SoC should be CSV
        print("File extension mismatch: SoC timeseries file should be of type .csv")
    if save_timeseries and Path(save_timeseries).suffix != ".csv":
        # timeseries data should be CSV
        print("File extension mismatch: timeseries file should be of type .csv")

    gc_ids = sorted(scenario.components.grid_connectors.keys())
    for gcID in gc_ids:
        if cost_calculation or save_timeseries:
            # aggregate timeseries info
            agg_ts = aggregate_timeseries(scenario, gcID)
        if save_results or testing:
            # aggregate GC dependent info
            results_file_content = aggregate_local_results(scenario=scenario, gcID=gcID)
        if save_results:
            # write general results to file
            fpath = Path(save_results)
            if len(gc_ids) > 1:
                # extend file name by GC name (without special characters)
                fpath = f"{fpath.stem}_{util.sanitize(gcID)}{fpath.suffix}"
            with fpath.open('w') as results_file:
                json.dump(results_file_content, results_file, indent=2)
        if save_timeseries:
            # save power use for each timestep in file
            fpath = Path(save_timeseries)
            if len(gc_ids) > 1:
                fpath = f"{fpath.stem}_{util.sanitize(gcID)}{fpath.suffix}"
            with fpath.open('w') as timeseries_file:
                # write header
                timeseries_file.write(','.join(agg_ts["header"]))
                # write timestep data
                for row in agg_ts["timeseries"]:
                    timeseries_file.write('\n' + ','.join(map(lambda x: str(x), row)))

    # GC-independent stuff

    if attach_vehicle_soc or save_soc:
        # generate (continuous) SoC of vehicles
        scenario.vehicle_socs = {}
        generate_soc_timeseries(scenario=scenario)
    if save_soc:
        # write vehicle SoC per timestep to file
        vids = sorted(scenario.components.vehicles.keys())
        with open(save_soc, "w") as soc_file:
            # write header
            header = ["timestep", "time"] + vids
            soc_file.write(','.join(header))
            for idx, r in enumerate(scenario.results):
                # general info: timestep index and timestamp
                # TZ removed for spreadsheet software
                row = [idx, r['current_time'].replace(tzinfo=None).isoformat()]

                row += [scenario.vehicle_socs[vid][idx] for vid in vids]
                # write row to file
                soc_file.write('\n' + ','.join(map(lambda x: str(x), row)))

    if visual or testing:
        aggregate_global_results(scenario)
    if visual:
        # plot!
        plot(scenario)
    if testing:
        # metadata, used in tests
        scenario.testing = {
            "timeseries": {
                "total_load": scenario.all_totalLoad,
                "prices": scenario.prices,
                "schedule": scenario.gcWindowSchedule,
                "sum_cs": scenario.sum_cs,
                "loads": scenario.loads
            },
            "max_total_load": max(scenario.all_totalLoad),
            "avg_flex_per_window": scenario.avg_flex_per_window,
            "sum_energy_per_window": scenario.sum_energy_per_window,
            "avg_stand_time": scenario.avg_stand_time,
            "avg_total_standing_time": scenario.avg_total_standing_time,
            "avg_needed_energy": scenario.avg_needed_energy,
            "avg_drawn_power": scenario.avg_drawn,
            "sum_feed_in_per_h": {gcID: (sum(scenario.feedInPower[gcID]) / scenario.stepsPerHour)
                                  for gcID in gc_ids},
            "vehicle_battery_cycles": {
                gcID: (scenario.total_vehicle_energy[gcID] / scenario.total_vehicle_cap[gcID])
                for gcID in gc_ids
            }
        }
