from copy import deepcopy
import datetime
import json
from os.path import relpath
from pathlib import Path
import warnings

from spice_ev import events, scenario, strategy, util

EPS = 1e-5


def generate_flex_band(scenario, gcID, core_standing_time=None):
    """ Generate flexibility potential for total vehicle fleet with perfect foresight.

    :param scenario: input scenario
    :type scenario: Scenario
    :param gcID: grid connector ID for which to create this flex band
    :type gcID: string
    :param core_standing_time: core standing time during which flexibility is guaranteed e.g.
        {"times":[{"start": [22,0], "end":[5,0]}], "no_drive_days":[6], "holidays": ["2022-01-01"]}
    :type core_standing_time: dict
    :return: flex band
    :rtype: dict
    """

    s = strategy.Strategy(
        scenario.components, scenario.start_time, **{
            "interval": scenario.interval,
            "margin": 1,
            "ALLOW_NEGATIVE_SOC": True
        })
    gc = s.world_state.grid_connectors[gcID]
    event_steps = scenario.events.get_event_steps(
        scenario.start_time, scenario.n_intervals, scenario.interval)

    ts_per_hour = datetime.timedelta(hours=1) / s.interval

    def clamp_to_gc(power):
        # helper function: make sure to stay within GC power limits
        return min(max(power, -gc.max_power), gc.max_power)

    # Collect and accumulate information about the entire fleet.
    # capacity of all vehicles combined [kWh]
    total_vehicle_capacity = 0
    # Total energy stored in fleet if all vehicles have SoC == desired SoC
    total_desired_energy = 0
    average_efficiency = 0
    # True if any vehicle has v2g enabled
    v2g_enabled = False
    for v in s.world_state.vehicles.values():
        total_vehicle_capacity += v.battery.capacity
        total_desired_energy += v.desired_soc * v.battery.capacity
        average_efficiency += v.battery.efficiency * v.battery.capacity
        if v.vehicle_type.v2g:
            v2g_enabled = True
    average_efficiency /= total_vehicle_capacity

    flex = {
        "min": [],
        "base": [],
        "max": [],
        "vehicles": {
            "capacity": total_vehicle_capacity,
            "desired_energy": total_desired_energy,
            "v2g": v2g_enabled,
            "efficiency": average_efficiency,
            "min": [],
            "max": [],
        },
        "batteries": {
            "stored": 0,
            "power": 0,
            "free": 0,  # how much energy can still be stored?
            "efficiency": 0  # average efficiency across all batteries
        },
        "intervals": [],
    }

    # get battery info: how much can be discharged in beginning, how much if fully charged?
    batteries = [b for b in s.world_state.batteries.values() if b.parent == gcID]
    bat_init_discharge_power = sum([b.get_available_power(s.interval) for b in batteries])
    for b in batteries:
        if b.capacity > 2**50:
            warnings.warn("battery without capacity detected")
        flex["batteries"]["stored"] += b.soc * b.capacity
        flex["batteries"]["power"] += b.loading_curve.max_power
        flex["batteries"]["free"] += (1 - b.soc) * b.capacity
        flex["batteries"]["efficiency"] += b.efficiency
        b.soc = 1
    bat_full_discharge_power = sum([b.get_available_power(s.interval) for b in batteries])
    flex["batteries"]["efficiency"] = \
        flex["batteries"]["efficiency"] / len(batteries) if len(batteries) else 1

    vehicles = {vid: [0, 0, 0] for vid in s.world_state.vehicles}
    vehicles_present = False
    prev_vehicles_present = False

    for step_i in range(scenario.n_intervals):
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            s.step(event_steps[step_i])

        current_datetime = scenario.start_time + scenario.interval * step_i
        currently_in_core_standing_time = \
            util.dt_within_core_standing_time(current_datetime, core_standing_time)

        # basic value: fixed load, local generation power
        base_flex = gc.get_current_load()

        # update vehicles
        for vid, v in s.world_state.vehicles.items():
            cs_id = v.connected_charging_station
            if cs_id is None:
                # vehicle not present: reset vehicle flex
                if (
                        vehicles[vid][0] != 0 and
                        core_standing_time is not None and
                        currently_in_core_standing_time):
                    warnings.warn(f"TS {step_i}: {vid} leaves during CST")
                # keep vehicle energy until charging interval is complete
                vehicles[vid] = [0, vehicles[vid][1], 0]
            else:
                cs = s.world_state.charging_stations[cs_id]
                if cs.parent == gcID:
                    if vehicles[vid][0] == 0:
                        # just arrived
                        charging_power = min(v.battery.loading_curve.max_power, cs.max_power)
                        delta_soc = max(v.get_delta_soc(), 0)
                        # scale with remaining steps
                        if v.estimated_time_of_departure is not None:
                            dep = v.estimated_time_of_departure
                            # try to understand this one
                            dep = -((scenario.start_time - dep) // s.interval)
                            factor = min((scenario.n_intervals - step_i) / (dep - step_i), 1)
                            delta_soc *= factor
                        vehicle_energy_needed = (
                            vehicles[vid][1] +
                            (delta_soc * v.battery.capacity) / v.battery.efficiency)
                        v.battery.soc = max(v.battery.soc, v.desired_soc)
                        v2g = (v.battery.get_available_power(s.interval)
                               * v.vehicle_type.v2g_power_factor) if v.vehicle_type.v2g else 0
                        vehicles[vid] = [charging_power, vehicle_energy_needed, v2g]
                        if (
                                step_i != 0 and
                                core_standing_time is not None and currently_in_core_standing_time):
                            warnings.warn(f"TS {step_i}: {vid} arrives during CST")
        num_vehicles_present = sum(bool(v[0]) for v in vehicles.values())

        local_generation_support = max(-base_flex, 0)
        vehicles_present = currently_in_core_standing_time and num_vehicles_present > 0
        if vehicles_present:
            # local generation surplus can support vehicle charging
            for v in vehicles.values():
                if local_generation_support <= EPS:
                    break
                power = min(v[0], v[1] * ts_per_hour, local_generation_support)
                v[1] -= power / ts_per_hour
                local_generation_support -= power
                base_flex += power

            # get sums from vehicles dict
            vehicle_flex, needed, v2g_flex = map(sum, zip(*vehicles.values()))
            if not prev_vehicles_present:
                # new standing period
                flex["intervals"].append({
                    "needed": 0,  # updated until all vehicles have left
                    "time": [],
                    "num_vehicles_present": 0,
                })
            info = flex["intervals"][-1]
            info["needed"] = needed
            info["num_vehicles_present"] = num_vehicles_present
            # only timesteps in core standing time are taken added to interval
            # if no core standing time is specified step_i is always appended
            # e.g. currently_in_core_standing_time = TRUE for all step_i
            if currently_in_core_standing_time:
                info["time"].append(step_i)
        else:
            # no vehicles present or not within core standing time: no vehicle flex
            vehicle_flex = 0
            v2g_flex = 0
            if prev_vehicles_present:
                # first TS with all vehicles left or end of CST
                # reset vehicle flex and energy needed
                vehicles = {vid: [0, 0, 0] for vid in vehicles}

        # take note if vehicles are present for comparison at next timestep
        prev_vehicles_present = vehicles_present

        bat_flex_discharge = bat_init_discharge_power if step_i == 0 else bat_full_discharge_power
        bat_flex_charge = flex["batteries"]["power"]
        # local generation surplus can also feed batteries
        local_gen_to_battery = min(bat_flex_charge, local_generation_support)
        bat_flex_charge -= local_gen_to_battery
        local_generation_support -= local_gen_to_battery

        flex["base"].append(clamp_to_gc(base_flex))
        # min: no vehicle charging, discharge from batteries and V2G
        flex["min"].append(clamp_to_gc(base_flex - bat_flex_discharge - v2g_flex))
        # max: all vehicle and batteries charging
        flex["max"].append(clamp_to_gc(base_flex + vehicle_flex + bat_flex_charge))
        flex["vehicles"]["min"].append(-v2g_flex)
        flex["vehicles"]["max"].append(vehicle_flex)

    return flex


def generate_individual_flex_band(scenario, gcID):
    """ Generate flexibility potential for individual vehicles with perfect foresight.

    :param scenario: input scenario
    :type scenario: Scenario
    :param gcID: grid connector ID for which to create this flex band
    :type gcID: string
    :return: flex band
    :rtype: dict
    """

    gc = deepcopy(scenario.components.grid_connectors[gcID])
    interval = scenario.interval

    event_signal_steps = scenario.events.get_event_steps(
        scenario.start_time, scenario.n_intervals, interval)
    # change ordering and corresponding interval from signal_time to start_time
    event_steps = [[] for _ in range(scenario.n_intervals)]
    for cur_events in event_signal_steps:
        for event in cur_events:
            # get start interval (ceil), must be within scenario time
            start_interval = -((scenario.start_time - event.start_time) // interval)
            if 0 <= start_interval < scenario.n_intervals:
                event_steps[start_interval].append(event)

    flex = {
        "vehicles": [[]],
        "batteries": {
            "stored": 0,
            "power": 0,
            "free": 0,  # how much energy can still be stored in batteries?
            "efficiency": 0,  # average efficiency across all batteries
            "init_discharge": 0,  # discharging power in beginning
            "full_discharge": 0,  # discharging power when fully charged
        },
        "base": [],
        "min": [],
        "max": [],
    }

    # aggregate battery info
    batteries = [deepcopy(b) for b in scenario.components.batteries.values() if b.parent == gcID]
    flex["batteries"]["init_discharge"] = sum([b.get_available_power(interval) for b in batteries])
    for b in batteries:
        if b.capacity > 2**50:
            warnings.warn("WARNING: battery without capacity detected")
        flex["batteries"]["stored"] += b.soc * b.capacity
        flex["batteries"]["power"] += b.loading_curve.max_power
        flex["batteries"]["free"] += (1 - b.soc) * b.capacity
        flex["batteries"]["efficiency"] += b.efficiency
        b.soc = 1
    flex["batteries"]["full_discharge"] = sum([b.get_available_power(interval) for b in batteries])
    flex["batteries"]["efficiency"] = \
        flex["batteries"]["efficiency"] / len(batteries) if len(batteries) else 1

    vehicles = deepcopy(scenario.components.vehicles)

    def get_v2g_energy(vehicle):
        if vehicle.vehicle_type.v2g:
            power_per_interval = vehicle.battery.get_available_power(interval)
            return power_per_interval * vehicle.vehicle_type.v2g_power_factor
        return 0

    # get initially connected vehicles
    for vid, v in vehicles.items():
        cs_id = v.connected_charging_station
        if cs_id is None:
            continue
        cs = scenario.components.charging_stations.get(cs_id)
        if cs is None or cs.parent != gcID:
            continue
        # connected
        v.last_arrival_idx = (0, len(flex["vehicles"][0]))
        delta_soc = max(v.desired_soc - v.battery.soc, 0)
        energy = delta_soc * v.battery.capacity / v.battery.efficiency
        flex["vehicles"][0].append({
            "vid": vid,
            "v2g": get_v2g_energy(v),
            "t_start": scenario.start_time,
            "t_end": scenario.stop_time,
            "idx_start": 0,
            "idx_end": scenario.n_intervals - 1,
            "init_soc": v.battery.soc,
            "energy": energy,
            "desired_soc": v.desired_soc,
            "efficiency": v.battery.efficiency,
            "p_min": max(cs.min_power, v.vehicle_type.min_charging_power),
            "p_max": min(cs.max_power, v.battery.loading_curve.max_power),
        })

    # update flex based on events
    for idx, timestep in enumerate(event_steps):
        if idx != 0:
            flex["vehicles"].append([])
        for event in timestep:
            if type(event) is events.FixedLoad and event.grid_connector_id == gcID:
                # fixed load event at this GC
                gc.current_loads[event.name] = event.value
            elif type(event) is events.LocalEnergyGeneration and event.grid_connector_id == gcID:
                # local generation event behind this GC
                gc.current_loads[event.name] = -event.value
            elif type(event) is events.GridOperatorSignal and event.grid_connector_id == gcID:
                # grid op event at this GC
                if gc.max_power:
                    if event.max_power is None:
                        # event max power not set: reset to connector power
                        gc.cur_max_power = gc.max_power
                    else:
                        gc.cur_max_power = min(gc.max_power, event.max_power)
                else:
                    # connector max power not set
                    gc.cur_max_power = event.max_power
            elif type(event) is events.VehicleEvent:
                # vehicle event: check if this GC
                vid = event.vehicle_id
                vehicle = vehicles[vid]
                if event.event_type == 'arrival':
                    if vehicle.connected_charging_station is not None:
                        warnings.warn("Multiple arrivals")
                    cs_id = event.update["connected_charging_station"]
                    vehicle.connected_charging_station = cs_id
                    vehicle.battery.soc += event.update["soc_delta"]
                    if cs_id is None:
                        continue
                    cs = scenario.components.charging_stations.get(cs_id)
                    if cs is None:
                        # CS not found? Can't charge
                        continue
                    if cs.parent != gcID:
                        # fake perfect charging
                        vehicle.battery.soc = max(vehicle.battery.soc, event.update["desired_soc"])
                        continue
                    # arrived at this GC: add to list
                    vehicle.last_arrival_idx = (len(flex["vehicles"])-1, len(flex["vehicles"][-1]))
                    delta_soc = event.update["desired_soc"] - vehicle.battery.soc
                    delta_soc = max(delta_soc, 0)
                    energy = delta_soc * vehicle.battery.capacity / vehicle.battery.efficiency
                    est_tod = event.update["estimated_time_of_departure"]
                    tod_idx = (est_tod - scenario.start_time) // scenario.interval
                    flex["vehicles"][-1].append({
                        "vid": vid,
                        "v2g": get_v2g_energy(vehicle),
                        "t_start": event.start_time,
                        "t_end": min(est_tod, scenario.stop_time),
                        "idx_start": idx,
                        "idx_end": min(tod_idx, scenario.n_intervals - 1),
                        "init_soc": vehicle.battery.soc,
                        "energy": energy,
                        "desired_soc": event.update["desired_soc"],
                        "efficiency": vehicle.battery.efficiency,
                        "p_min": max(cs.min_power, vehicle.vehicle_type.min_charging_power),
                        "p_max": min(cs.max_power, vehicle.battery.loading_curve.max_power),
                    })
                    vehicle.battery.soc = max(vehicle.battery.soc, event.update["desired_soc"])
                else:
                    # departure
                    cs_id = vehicle.connected_charging_station
                    if cs_id is None:
                        continue
                    vehicle.connected_charging_station = None
                    cs = scenario.components.charging_stations.get(cs_id)
                    if cs is None or cs.parent != gcID:
                        # leave without being connected or different GC: skip
                        continue
                    # departed from this GC: update departure time
                    v_idx = vehicle.last_arrival_idx
                    flex["vehicles"][v_idx[0]][v_idx[1]]["t_end"] = event.start_time
                    flex["vehicles"][v_idx[0]][v_idx[1]]["idx_end"] = idx
            # other event types ignored
        # end of current events: get current GC loads
        flex["base"].append(gc.get_current_load())
        flex["min"].append(-gc.cur_max_power)
        flex["max"].append(gc.cur_max_power)
    # end of timesteps
    return flex


def aggressive_round(f, places=0):
    """ Numbers close to zero are truncated to zero.

    :param f: number to round
    :type f: numeric
    :param places: decimal places to round to. Defaults to 0.
    :type places: int
    :return: rounded number
    :rtype: numeric
    """

    if -EPS < f < EPS:
        return 0
    return round(f, places)


def generate_schedule(args):
    """ Generate schedule for grid signals based on total vehicle fleet.

    :param args: input arguments
    :type args: argparse.Namespace
    """

    # read in scenario
    args.scenario = Path(args.scenario)
    with args.scenario.open('r') as f:
        scenario_json = json.load(f)
        scenario_json['events']['schedule_from_csv'] = {}
        s = scenario.Scenario(scenario_json, args.scenario.parent)

    ts_per_hour = datetime.timedelta(hours=1) / s.interval

    assert len(s.components.grid_connectors) == 1, "Only one grid connector supported"

    # compute flexibility potential (min/max) of single grid connector for each timestep
    gcID, gc = list(s.components.grid_connectors.items())[0]
    # use different function depending on "individual" argument
    core_standing_time = args.core_standing_time or s.core_standing_time
    if args.individual:
        flex = generate_individual_flex_band(s, gcID)
    else:
        flex = generate_flex_band(s, gcID=gcID, core_standing_time=core_standing_time)
        # check that core standing time is set
        if core_standing_time is None:
            warnings.warn("Core standing time is not set. "
                          "You can not simulate the schedule strategy without.")

    residual_load, curtailment, grid_start_time = util.read_grid_file(args.input)
    if grid_start_time is None:
        # if timestamp column does not exist or contains wrong format
        # assume grid situation timeseries at the same time as simulation
        grid_start_time = s.start_time.replace(tzinfo=None)

    # find timesteps relevant for simulation and discard remaining
    idx_start = (s.start_time.replace(tzinfo=None) - grid_start_time) // s.interval
    idx_start = idx_start if 0 < idx_start < len(residual_load) else 0
    idx_end = min(idx_start + s.n_intervals, len(residual_load))
    residual_load = residual_load[idx_start:idx_end]
    curtailment = curtailment[idx_start:idx_end]

    # zero-pad for same length as scenario
    residual_load += [0] * (s.n_intervals - len(residual_load))
    curtailment += [0] * (s.n_intervals - len(curtailment))

    # save original curtailment and residual load
    original_curtailment = deepcopy(curtailment)
    original_residual_load = deepcopy(residual_load)

    # default schedule: just basic power needs, but clipped to GC power
    schedule = [min(max(v, flex["min"][i]), flex["max"][i]) for i, v in enumerate(flex["base"])]

    # adjust curtailment and residual load based on base flex (local generation / fixed load)
    for i, power in enumerate(flex["base"]):
        curtailment_power = max(min(curtailment[i], power), 0)
        curtailment[i] -= curtailment_power
        residual_load[i] += power - curtailment_power
        flex["base"][i] = 0

    vehicle_ids = sorted(s.components.vehicles.keys())
    vehicle_schedule = {vid: [0] * s.n_intervals for vid in vehicle_ids}

    def distribute_energy_balanced(period, energy_needed, v2g, ind_flex, vid=None):
        """ Distribute energy across a time period.

        The algorithm tries to distribute energy such that grid power (residual load) is balanced.
        Curtailment power has priority when charging.

        :param period: timestep indices when the energy is distributed
        :type period: iterable
        :param energy_needed: Amount of energy to be distributed
        :type energy_needed: float
        :param v2g: general discharge capability (also true for batteries)
        :type v2g: bool
        :param ind_flex: individual flex. Power must stay within given bounds
        :type ind_flex: list of tuples
        :param vid: vehicle ID. Used for individual schedule. Optional.
        :type vid: string
        :return: total change in stored energy after distribution completes
        """

        power_needed = energy_needed * ts_per_hour
        energy_distributed = 0
        # lower and upper bounds of individual flex and available power
        p_low = min([max(ind_flex[i][0], -avail["min"][j]) for i, j in enumerate(period)])
        p_high = max([min(ind_flex[i][1], avail["max"][j]) for i, j in enumerate(period)])
        # average residual load during period
        p_avg = 0
        for idx, i in enumerate(period):
            # use curtailment power first (greedy charging)
            if curtailment[i] > EPS:
                power = min(curtailment[i], avail["max"][i], power_needed, ind_flex[idx][1])
                power_needed -= power
                schedule[i] += power
                avail["min"][i] += power
                avail["max"][i] -= power
                if vid:
                    vehicle_schedule[vid][i] += power
                curtailment[i] -= power
                energy_distributed += power / ts_per_hour
                ind_flex[idx] = (ind_flex[idx][0] - power, ind_flex[idx][1] - power)
            p_avg += residual_load[i] - curtailment[i]
        p_avg /= len(period)

        if power_needed < EPS and not v2g:
            # no power needed and no discharging capabilities: finished
            return energy_distributed

        # find cutoff for peak shaving such that in times of low res. load vehicles are charging
        old_power_needed = power_needed
        power = [0]*len(period)
        # good first approx: average out residual load and add needed average power
        p = p_avg + power_needed / len(period)
        while (p_high - p_low) > EPS:
            power_needed = old_power_needed
            new_avg = p_avg + p
            for idx, i in enumerate(period):
                # difference of new average to residual load
                delta_p = new_avg - (residual_load[i] - curtailment[i])
                if delta_p > 0:
                    # res. load lower than new average: charge to increase load
                    # clip to available power
                    delta_p = min(delta_p, avail["max"][i])
                    # clip to flex (individual and global)
                    delta_p = min(delta_p, ind_flex[idx][1], flex["max"][i] - schedule[i])
                elif v2g and residual_load[i] > EPS and curtailment[i] < EPS:
                    # V2G only if res. load positive and no curtailment
                    # clip to available power (which is positive)
                    delta_p = max(delta_p, -avail["min"][i])
                    # clip to flex (individual and global)
                    delta_p = max(delta_p, ind_flex[idx][0], flex["min"][i] - schedule[i])
                else:
                    # res. load higher than average (delta_p negative), but not V2G: skip
                    power[idx] = 0
                    continue
                power[idx] = delta_p
                power_needed -= delta_p

            if power_needed > EPS:
                # power not sufficient
                p_low = p
            elif power_needed < -EPS:
                # too much power drawn
                p_high = p
            else:
                # power need exactly fulfilled
                break
            # approach optimum through binary search tree
            p = (p_low + p_high) / 2

        # apply power
        for idx, i in enumerate(period):
            schedule[i] += power[idx]
            if vid:
                vehicle_schedule[vid][i] += power[idx]
            curtail_power = max(min(curtailment[i], power[idx]), 0)
            curtailment[i] -= curtail_power
            residual_load[i] += power[idx] - curtail_power
            energy_distributed += power[idx] / ts_per_hour
            avail["min"][i] += power[idx]
            avail["max"][i] -= power[idx]

        return energy_distributed

    if args.individual:
        # available GC power: max power minus base load
        avail = {
            "min": [max(schedule[i] - flex["min"][i], 0) for i in range(s.n_intervals)],
            "max": [max(flex["max"][i] - schedule[i], 0) for i in range(s.n_intervals)],
        }
        flex["min"] = schedule.copy()
        flex["max"] = schedule.copy()

        for i in range(s.n_intervals):
            # sort arrivals by energy needed and standing time
            # prioritize higher power needed
            vehicles_arriving = sorted(
                flex["vehicles"][i],
                key=lambda v: -v["energy"] / (v["t_end"] - v["t_start"]).total_seconds())
            for vinfo in vehicles_arriving:
                if vinfo["idx_start"] >= vinfo["idx_end"]:
                    # arrival/departure same interval: ignore
                    continue

                standing_range = range(vinfo["idx_start"], vinfo["idx_end"])
                # add to flex
                for j in standing_range:
                    flex["min"][j] -= vinfo["v2g"]
                    flex["max"][j] += vinfo["p_max"]

                distribute_energy_balanced(
                    standing_range,
                    energy_needed=vinfo["energy"],
                    v2g=bool(vinfo["v2g"]),
                    vid=vinfo["vid"],
                    ind_flex=[[-vinfo["v2g"], vinfo["p_max"]] for _ in standing_range])

            # add battery flex
            if i == 0:
                flex["min"][i] -= flex["batteries"]["init_discharge"]
            else:
                flex["min"][i] -= flex["batteries"]["full_discharge"]
            bat_flex = flex["batteries"]["power"] * flex["batteries"]["efficiency"] / ts_per_hour
            flex["max"][i] += bat_flex
            # end generate schedule for individual vehicles
    else:
        # generate schedule for whole vehicle park
        avail = {
            "min": [max(schedule[i] + gc.max_power, 0) for i in range(s.n_intervals)],
            "max": [max(gc.max_power - schedule[i], 0) for i in range(s.n_intervals)],
        }
        for interval in flex["intervals"]:
            if not interval["time"]:
                # empty interval
                continue
            distribute_energy_balanced(
                interval["time"],
                energy_needed=interval["needed"],
                v2g=flex["vehicles"]["v2g"],
                ind_flex=[
                    [flex["vehicles"]["min"][i], flex["vehicles"]["max"][i]]
                    for i in interval["time"]])

    # create schedule for batteries
    batteries = flex["batteries"]  # members: stored, power, free
    if batteries["power"]:
        distribute_energy_balanced(
            range(s.n_intervals),
            energy_needed=-batteries["stored"] * batteries["efficiency"] / ts_per_hour,
            v2g=True,
            ind_flex=[[-batteries["power"], batteries["power"]] for _ in range(s.n_intervals)])

    # check that schedule is within flex
    for i, v in enumerate(schedule):
        assert flex["min"][i] - EPS < v < flex["max"][i] + EPS, (
            f"Schedule outside flex @ {i}: {v} not within [{flex['min'][i]}, {flex['max'][i]}]")

    try:
        args.output = Path(args.output)
    except TypeError:
        # no output filename given: save in same directory as scenario
        args.output = args.scenario.parent / f"{args.scenario.stem}_schedule.csv"
    print("Writing to", args.output)
    # pathlib relative_to can only look in subdirectories -> use os.path.relpath
    relative_output_path = relpath(args.output, args.scenario.parent)
    # write schedule to file
    with args.output.open('w') as f:
        # header
        header = ["timestamp", "schedule [kW]", "charge", "residual load old [kW]",
                  "curtailment old [kW]", "residual load new [kW]", "curtailment new [kW]"]
        if args.individual:
            header += vehicle_ids
        f.write(', '.join(header) + '\n')
        cur_time = s.start_time - s.interval
        for t in range(s.n_intervals):
            cur_time += s.interval
            # charging window: curtailment present or res. load negative
            charging_window = (curtailment[i] > EPS) or (residual_load[t] < -EPS)
            values = [
                cur_time.isoformat(),  # timestamp
                aggressive_round(schedule[t], 3),  # schedule rounded to Watts
                int(charging_window),
                round(original_residual_load[t], 3),
                round(original_curtailment[t], 3),
                round(residual_load[t], 3),
                round(curtailment[t], 3),
            ]
            if args.individual:
                # create column for every vehicle schedule
                values += [aggressive_round(vehicle_schedule[vid][t], 3) for vid in vehicle_ids]
            f.write(', '.join([str(v) for v in values]) + '\n')

    # add schedule file info to scenario JSON
    scenario_json['events']['schedule_from_csv'] = {
        'column': 'schedule [kW]',
        'start_time': s.start_time.isoformat(),
        'step_duration_s': s.interval.seconds,
        'csv_file': relative_output_path,
        'grid_connector_id': list(s.components.grid_connectors.keys())[0],
        'individual': args.individual,
    }
    scenario_json['scenario']['core_standing_time'] = core_standing_time
    with args.scenario.open('w') as f:
        json.dump(scenario_json, f, indent=2)

    if args.visual:
        # plot flex with schedule, input and priorities
        import matplotlib.pyplot as plt

        fig, axes = plt.subplots(2, 1)
        # plot flex and schedule
        axes[0].step(
            range(s.n_intervals),
            list(zip(flex["min"], flex["max"], schedule)),
            label=["min. flexibility", "max. flexibility", "schedule"])
        axes[0].axhline(color='k', linestyle='dotted', linewidth=1)
        axes[0].set_xlim([0, s.n_intervals])
        axes[0].legend()
        axes[0].set_ylabel("power [kW]")
        # plot input file
        axes[1].step(
            range(s.n_intervals),
            list(zip(residual_load, curtailment)),
            label=["residual load (new)", "curtailment (new)"])
        # reset color cycle, so lines of original data have same color
        axes[1].set_prop_cycle(None)
        axes[1].step(
            range(s.n_intervals),
            list(zip(original_residual_load, original_curtailment)),
            linestyle='--', label=["residual load (original)", "curtailment (original)"])
        # show cutoffs
        axes[1].axhline(color='k', linestyle='dotted', linewidth=1)
        axes[1].legend()
        axes[1].set_xlim([0, s.n_intervals])
        axes[1].set_ylabel("power [kW]")
        plt.show()
