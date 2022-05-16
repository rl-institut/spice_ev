import datetime
import os


def get_json_results(scenario, gcID, results, n_steps, stepsPerHour,
                     socs, extLoads, totalLoad, batteryLevels, feedInPower):

    json_results = {}
    flex = scenario.agg_results['flex_band'][gcID]

    # gather info about standing and power in specific time windows
    load_count = [[0] for _ in scenario.constants.vehicles]
    load_window = [[] for _ in range(4)]
    count_window = [[0]*len(scenario.constants.vehicles) for _ in range(4)]

    cur_time = scenario.start_time - scenario.interval
    # maximum power (fixed and variable loads)
    max_fixed_load = 0
    max_variable_load = 0
    for idx in range(n_steps):
        cur_time += scenario.interval
        time_since_midnight = cur_time - cur_time.replace(hour=0, minute=0)
        # four equally large timewindows: 04-10, 10-16, 16-22, 22-04
        # shift time by four hours
        shifted_time = time_since_midnight - datetime.timedelta(hours=4)
        # compute window index
        widx = (shifted_time // datetime.timedelta(hours=6)) % 4

        load_window[widx].append((flex["max"][idx] - flex["min"][idx],
                                  totalLoad[gcID][idx]))
        count_window[widx] = list(map(
            lambda c, t: c + (t is not None),
            count_window[widx], socs[idx]))

        for i, soc in enumerate(socs[idx]):
            if soc is None and load_count[i][-1] > 0:
                load_count[i].append(0)
            else:
                load_count[i][-1] += (soc is not None)

        fixed_load = sum([v for k, v in extLoads[gcID][idx].items() if
                          k in scenario.events.external_load_lists or
                          k in scenario.events.energy_feed_in_lists])
        max_fixed_load = max(max_fixed_load, fixed_load)
        var_load = totalLoad[gcID][idx] - fixed_load
        max_variable_load = max(max_variable_load, var_load)

    # avg flex per window
    avg_flex_per_window = scenario.agg_results['avg_flex_per_window']
    avg_flex_per_window[gcID] = [sum([t[0] for t in w]) / len(w) if w else 0
                                 for w in load_window]
    json_results["avg flex per window"] = {
        "04-10": avg_flex_per_window[gcID][0],
        "10-16": avg_flex_per_window[gcID][1],
        "16-22": avg_flex_per_window[gcID][2],
        "22-04": avg_flex_per_window[gcID][3],
        "unit": "kW",
        "info": "Average flexible power range per time window"
    }

    # sum of used energy per window
    sum_energy_per_window = scenario.agg_results['sum_energy_per_window']
    sum_energy_per_window[gcID] = [sum([t[1] for t in w]) / stepsPerHour
                                   for w in load_window]
    json_results["sum of energy per window"] = {
        "04-10": sum_energy_per_window[gcID][0],
        "10-16": sum_energy_per_window[gcID][1],
        "16-22": sum_energy_per_window[gcID][2],
        "22-04": sum_energy_per_window[gcID][3],
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
    avg_stand_time = scenario.agg_results['avg_stand_time']
    if num_loads > 0:
        avg_stand_time[gcID] = sum(map(sum, load_count)) / stepsPerHour / num_loads
    else:
        avg_stand_time[gcID] = 0
    # avg total standing time
    # count per car: list(zip(*count_window))
    total_standing = sum(map(sum, count_window))
    avg_total_standing_time = scenario.agg_results['avg_total_standing_time']
    avg_total_standing_time[gcID] = total_standing / len(
        scenario.constants.vehicles) / stepsPerHour
    json_results["avg standing time"] = {
        "single": avg_stand_time[gcID],
        "total": avg_total_standing_time,
        "unit": "h",
        "info": "Average duration of a single standing event and "
                "average total time standing time of all vehicles"
    }

    # percent of standing time in time window
    perc_stand_window = scenario.agg_results['perc_stand_window']
    perc_stand_window[gcID] = list(map(
        lambda x: x * 100 / total_standing if total_standing > 0 else 0,
        map(sum, count_window)))
    json_results["standing per window"] = {
        "04-10": perc_stand_window[gcID][0],
        "10-16": perc_stand_window[gcID][1],
        "16-22": perc_stand_window[gcID][2],
        "22-04": perc_stand_window[gcID][3],
        "unit": "%",
        "info": "Share of standing time per time window"
    }

    # avg needed energy per standing period
    intervals = flex["intervals"]
    avg_needed_energy = scenario.agg_results['avg_needed_energy']
    if intervals:
        avg_needed_energy[gcID] = sum([i["needed"] / i["num_cars_present"] for i in
                                       intervals]) / len(intervals)
    else:
        avg_needed_energy[gcID] = 0
    json_results["avg needed energy"] = {
        # avg energy per standing period and vehicle
        "value": avg_needed_energy[gcID],
        "unit": "kWh",
        "info": "Average amount of energy needed to reach the desired SoC"
                " (averaged over all vehicles and charge events)"
    }

    # power peaks (fixed loads and variable loads)
    if any(totalLoad[gcID]):
        json_results["power peaks"] = {
            "fixed": max_fixed_load,
            "variable": max_variable_load,
            "total": max(totalLoad[gcID]),
            "unit": "kW",
            "info": "Maximum drawn power, by fixed loads (building, PV),"
                    " variable loads (charging stations, stationary batteries) "
                    "and all loads"
        }

    # average drawn power
    avg_drawn = scenario.agg_results['avg_drawn']
    avg_drawn[gcID] = sum(totalLoad[gcID]) / n_steps if n_steps > 0 else 0
    json_results["avg drawn power"] = {
        "value": avg_drawn[gcID],
        "unit": "kW",
        "info": "Drawn power, averaged over all time steps"
    }

    # total feed-in energy
    json_results["feed-in energy"] = {
        "value": sum(feedInPower[gcID]) / stepsPerHour,
        "unit": "kWh",
        "info": "Total energy from renewable energy sources"
    }

    # battery sizes
    for b in batteryLevels.values():
        if any(b):
            bat_dict = {batName: max(values) for batName, values in
                        batteryLevels.items()}
            bat_dict.update({
                "unit": "kWh",
                "info": "Maximum stored energy in each battery by name"
            })
            json_results["max. stored energy in batteries"] = bat_dict

    # charging cycles
    # stationary batteries
    total_bat_cap = 0
    for batID, battery in scenario.constants.batteries.items():
        if scenario.constants.batteries[batID].parent == gcID:
            if battery.capacity > 2**63:
                # unlimited capacity
                max_cap = max(batteryLevels[batID])
                print("Battery {} is unlimited, set capacity to {} kWh".format(
                    batID, max_cap))
                total_bat_cap += max_cap
            else:
                total_bat_cap += battery.capacity
    if total_bat_cap:
        total_bat_energy = 0
        for loads in extLoads[gcID]:
            for batID in scenario.constants.batteries.keys():
                if scenario.constants.batteries[batID].parent == gcID:
                    total_bat_energy += max(loads.get(batID, 0), 0) / stepsPerHour
        json_results["stationary battery cycles"] = {
            "value": total_bat_energy / total_bat_cap,
            "unit": None,
            "info": "Number of load cycles of stationary batteries (averaged)"
        }
    # vehicles
    total_car_cap = scenario.agg_results['total_car_cap']
    total_car_cap[gcID] = sum([v.battery.capacity for v in
                               scenario.constants.vehicles.values()])
    total_car_energy = scenario.agg_results['total_car_energy']
    total_car_energy[gcID] = sum([sum(map(
        lambda v: max(v, 0), r["commands"].values())) for r in results])
    json_results["all vehicle battery cycles"] = {
        "value": total_car_energy[gcID]/total_car_cap[gcID],
        "unit": None,
        "info": "Number of load cycles per vehicle (averaged)"
    }

    return json_results


def save_timeseries(scenario, gc_ids, output_path, results,
                    gcPowerSchedule, gcWindowSchedule, extLoads, prices,
                    feedInPower, batteryLevels, connChargeByTS, totalLoad):
    # check file extension
    file_name, ext = os.path.splitext(output_path)
    if ext != ".csv":
        print("File extension mismatch: timeseries file is of type .csv")

    for gcID in gc_ids:
        if len(gc_ids) == 1:
            filename = output_path
        else:
            filename = f"{file_name}_{gcID}{ext}"

        cs_ids = sorted(item for item in scenario.constants.charging_stations.keys() if
                        scenario.constants.charging_stations[item].parent == gcID)

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

        round_to_places = 2

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

        scheduleKeys = []
        for gcID in sorted(gcPowerSchedule.keys()):
            if any(s is not None for s in gcPowerSchedule[gcID]):
                scheduleKeys.append(gcID)

        # any loads except CS present?
        hasExtLoads = any(extLoads)

        with open(filename, 'w') as timeseries_file:
            # write header
            # general info
            header = ["timestep", "time"]
            # price
            if any(prices):
                # external loads (e.g., building)
                header.append("price [EUR/kWh]")
            # grid power
            header.append("grid power [kW]")
            # external loads
            if hasExtLoads:
                # external loads (e.g., building)
                header.append("ext.load [kW]")
            # feed-in
            if any(feedInPower):
                header.append("feed-in [kW]")
            # batteries
            if scenario.constants.batteries:
                header += ["battery power [kW]", "bat. stored energy [kWh]"]
            # flex + schedule
            header += ["flex min [kW]", "flex base [kW]", "flex max [kW]"]
            header += ["schedule {} [kW]".format(gcID) for gcID in scheduleKeys]
            header += ["window {}".format(gcID) for gcID in scheduleKeys]
            # sum of charging power
            header.append("sum CS power")
            # charging power per use case
            header += ["sum UC {}".format(uc) for uc in uc_keys_present]
            # total number of occupied charging stations
            header.append("# occupied CS")
            # number of occupied CS per UC
            header += ["# occupied UC {}".format(uc) for uc in uc_keys_present]
            # charging power per CS
            header += [str(cs_id) for cs_id in cs_ids]
            timeseries_file.write(','.join(header))

            # write timesteps
            for idx, r in enumerate(results):
                # general info: timestep index and timestamp
                # TZ removed for spreadsheet software
                row = [idx, r['current_time'].replace(tzinfo=None)]
                # price
                if any(prices[gcID]):
                    row.append(round(prices[gcID][idx][0], round_to_places))
                # grid power (negative since grid power is fed into system)
                row.append(-1 * round(totalLoad[gcID][idx], round_to_places))
                # external loads
                if hasExtLoads:
                    sumExtLoads = sum([
                        v for k, v in extLoads[gcID][idx].items()
                        if k in scenario.events.external_load_lists])
                    row.append(round(sumExtLoads, round_to_places))
                # feed-in (negative since grid power is fed into system)
                if any(feedInPower):
                    row.append(-1 * round(feedInPower[gcID][idx], round_to_places))
                # batteries
                if scenario.constants.batteries:
                    current_battery = {}
                    for batID in batteryLevels:
                        if scenario.constants.batteries[batID].parent == gcID:
                            current_battery.update({batID: batteryLevels[batID]})
                    row += [
                        # battery power
                        round(sum([
                            v for k, v in extLoads[gcID][idx].items()
                            if k in scenario.constants.batteries]),
                            round_to_places),
                        # battery levels
                        # get connected battery
                        round(
                            sum([levels[idx] for levels in current_battery.values()]),
                            round_to_places
                        )
                    ]
                # flex
                flex = scenario.agg_results['flex_band'][gcID]
                row += [
                    round(flex["min"][idx], round_to_places),
                    round(flex["base"][idx], round_to_places),
                    round(flex["max"][idx], round_to_places)
                ]
                # schedule + window schedule
                row += [
                    round(gcPowerSchedule[gcID][idx], round_to_places)
                    for gcID in scheduleKeys]
                row += [
                    round(gcWindowSchedule[gcID][idx], round_to_places)
                    for gcID in scheduleKeys]
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
                row.append(len(connChargeByTS[gcID][idx]))
                # get number of occupied CS at gc for each use case
                row += [
                    sum([1 if uc_key in cs_id else 0
                        for cs_id in connChargeByTS[gcID][idx]]) for uc_key in
                    uc_keys_present]
                # get individual charging power of cs_id that is connected to gc
                row += [round(gc_commands.get(cs_id, 0), round_to_places) for cs_id in
                        cs_ids]
                # write row to file
                timeseries_file.write('\n' + ','.join(map(lambda x: str(x), row)))


def save_soc(output_path, scenario, results, socs, disconnect):
    # save soc of each vehicle in one file

    # check file extension
    ext = os.path.splitext(output_path)[-1]
    if ext != ".csv":
        print("File extension mismatch: timeseries file is of type .csv")
    with open(output_path, "w+") as soc_file:
        # write header
        header_s = ["timestep", "time"]
        for vidx, vid in enumerate(sorted(scenario.constants.vehicles.keys())):
            header_s.append(vid)
        soc_file.write(','.join(header_s))

        # combine SOCs from connected and disconnected timesteps
        # for every time step and vehicle, exactly one of the two has
        # a numeric value while the other contains a NoneType
        continuous_soc = [[s or d for s, d in zip(socs_ts, disconnect_ts)]
                          for socs_ts, disconnect_ts in zip(socs, disconnect)]

        for idx, r in enumerate(results):
            # general info: timestep index and timestamp
            # TZ removed for spreadsheet software
            row_s = [idx, r['current_time'].replace(tzinfo=None).isoformat()]
            row_s += continuous_soc[idx]

            # write row to file
            soc_file.write('\n' + ','.join(map(lambda x: str(x), row_s)))

        # store timeseries in a dictionary accessible by the Scenario object
        # in order to avoid computing it again in subsequent sections
        continuous_soc = list(map(list, zip(*continuous_soc)))
        scenario.agg_results.update({
            'soc_timeseries': {
                v_id: soc for v_id, soc in
                zip(scenario.constants.vehicles.keys(), continuous_soc)
            }
        })
