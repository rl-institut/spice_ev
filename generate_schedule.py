#!/usr/bin/env python3

import argparse
import csv
import datetime
import json
from json.decoder import JSONDecodeError
import os
import warnings

from src import scenario, strategy, util

EPS = 1e-8


def generate_flex_band(scenario, core_standing_time=None):
    # generate flexibility potential with perfect foresight

    assert len(scenario.constants.grid_connectors) == 1, "Only one grid connector supported"
    gc = list(scenario.constants.grid_connectors.values())[0]

    # generate basic strategy
    s = strategy.Strategy(
        scenario.constants, scenario.start_time, **{"interval": scenario.interval})
    event_steps = scenario.events.get_event_steps(
        scenario.start_time, scenario.n_intervals, scenario.interval)

    ts_per_hour = datetime.timedelta(hours=1) / s.interval

    def clamp_to_gc(power):
        # helper function: make sure to stay within GC power limits
        return min(max(power, -gc.max_power), gc.max_power)

    total_vehicle_capacity = 0
    total_desired_energy = 0
    average_efficiency = 0
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
            "v2g_power_factor": 1,
            "discharge_limit": 0.5,
            "capacity": total_vehicle_capacity,
            "desired_energy": total_desired_energy,
            "v2g": v2g_enabled,
            "efficiency": 0.95
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
    batteries = s.world_state.batteries.values()
    bat_init_discharge_power = sum([b.get_available_power(s.interval) for b in batteries])
    for b in batteries:
        if b.capacity > 2**50:
            print("WARNING: battery without capacity detected")
        flex["batteries"]["stored"] += b.soc * b.capacity
        flex["batteries"]["power"] += b.loading_curve.max_power
        flex["batteries"]["free"] += (1 - b.soc) * b.capacity
        flex["batteries"]["efficiency"] += b.efficiency
        b.soc = 1
    bat_full_discharge_power = sum([b.get_available_power(s.interval) for b in batteries])
    flex["batteries"]["efficiency"] = \
        flex["batteries"]["efficiency"] / len(batteries) if len(batteries) else 1

    cars = {vid: [0, 0, 0] for vid in s.world_state.vehicles}
    vehicles_present = False
    power_needed = 0

    for step_i in range(scenario.n_intervals):
        s.step(event_steps[step_i])

        current_datetime = scenario.start_time + scenario.interval * step_i
        currently_in_core_standing_time = \
            util.dt_within_core_standing_time(current_datetime, core_standing_time)

        # basic value: external load, feed-in power
        base_flex = sum([gc.get_current_load() for gc in s.world_state.grid_connectors.values()])

        num_cars_present = 0

        # update vehicles
        for vid, v in s.world_state.vehicles.items():
            if v.connected_charging_station is None:
                # vehicle not present: reset info, add to power needed in last interval
                power_needed += cars[vid][1]
                cars[vid] = [0, 0, 0]
            else:
                num_cars_present += 1
                if cars[vid][0] == 0:
                    # just arrived
                    charging_power = v.battery.loading_curve.max_power
                    delta_soc = max(v.get_delta_soc(), 0)
                    # scale with remaining steps
                    if v.estimated_time_of_departure is not None:
                        dep = v.estimated_time_of_departure
                        # try to understand this one
                        dep = -((scenario.start_time - dep) // s.interval)
                        factor = min((scenario.n_intervals - step_i) / (dep - step_i), 1)
                        delta_soc *= factor
                    vehicle_energy_needed = (delta_soc * v.battery.capacity) / v.battery.efficiency
                    v.battery.soc = max(v.battery.soc, v.desired_soc)
                    v2g = v.battery.get_available_power(s.interval) if v.vehicle_type.v2g else 0
                    cars[vid] = [charging_power, vehicle_energy_needed, v2g]

        pv_support = max(-base_flex, 0)
        if num_cars_present:
            # PV surplus can support vehicle charging
            for v in cars.values():
                if pv_support <= EPS:
                    break
                power = min(v[0], v[1] * ts_per_hour, pv_support)
                v[1] -= power / ts_per_hour
                pv_support -= power
                base_flex += power

            # get sums from cars dict
            vehicle_flex, needed, v2g_flex = map(sum, zip(*cars.values()))
            if not vehicles_present:
                # new standing period
                flex["intervals"].append({
                    "needed": 0,
                    "time": []
                })
            info = flex["intervals"][-1]
            info["needed"] = needed
            # only timesteps in core standing time are taken added to interval
            # if no core standing time is specified step_i is always appended
            # e.g. currently_in_core_standing_time = TRUE for all step_i
            if currently_in_core_standing_time:
                info["time"].append(step_i)
        else:
            # all vehicles left
            if vehicles_present:
                # first TS with all vehicles left: update power needed
                flex["intervals"][-1]["needed"] = power_needed
            vehicle_flex = power_needed = v2g_flex = 0
        vehicles_present = num_cars_present > 0

        bat_flex_discharge = bat_init_discharge_power if step_i == 0 else bat_full_discharge_power
        bat_flex_charge = flex["batteries"]["power"]
        # PV surplus can also feed batteries
        pv_to_battery = min(bat_flex_charge, pv_support)
        bat_flex_charge -= pv_to_battery
        pv_support -= pv_to_battery

        flex["base"].append(clamp_to_gc(base_flex))
        # min: no vehicle charging, discharge from batteries and V2G
        flex["min"].append(clamp_to_gc(base_flex - bat_flex_discharge - v2g_flex))
        # max: all vehicle and batteries charging
        flex["max"].append(clamp_to_gc(base_flex + vehicle_flex + bat_flex_charge))

    return flex


def generate_schedule(args):

    # read in scenario
    with open(args.scenario, 'r') as f:
        scenario_json = json.load(f)
        scenario_json['events']['schedule_from_csv'] = {}
        s = scenario.Scenario(scenario_json, os.path.dirname(args.scenario))

    ts_per_hour = datetime.timedelta(hours=1) / s.interval

    # compute flexibility potential (min/max) for each timestep
    flex = generate_flex_band(s, args.core_standing_time)

    netto = []
    curtailment = []
    # Read NSM timeseries
    with open(args.input, 'r', newline='') as f:
        reader = csv.DictReader(f)
        for row_idx, row in enumerate(reader):
            # get start time of NSM time series
            if row_idx == 0:
                try:
                    nsm_start_time = datetime.datetime.strptime(row["timestamp"], "%Y-%m-%d %H:%M")
                except (ValueError, KeyError):
                    # if timestamp column does not exist or contains wrong format
                    # assume NSM timeseries at the same time as simulation
                    nsm_start_time = s.start_time.replace(tzinfo=None)
                    warnings.warn('Time component of NSM timeseries ignored. '
                                  'Must be of format YYYY.MM.DD HH:MM')
            # store netto value, use previous value if none provided
            try:
                netto.append(float(row["netto"]))
            except ValueError:
                warnings.warn("Netto timeseries contains non-numeric values.")
                replace_unknown = netto[-1] if row_idx > 0 else 0
                netto.append(replace_unknown)
            # store curtailment info
            try:
                curtailment.append(-float(row["curtailment"]))
            except ValueError:
                warnings.warn("Curtailment timeseries contains non-numeric values.")
                replace_unknown = curtailment[-1] if row_idx > 0 else 0
                curtailment.append(replace_unknown)

    # priorities: times of curtailment + percentile with lowest load (1),
    #             negative loads (2), positive loads (3), percentile with highest load (4)
    # Note: The procedure to determine priorities for every timestep assumes that
    # time intervals of simulation are equal to time intervals in NSM time series.

    # compute cutoff values for priorities 1 and 4 using all netto values
    idx_percentile = int(len(netto) * args.priority_percentile)
    sorted_netto = sorted(netto)
    cutoff_priority_1 = sorted_netto[idx_percentile]
    cutoff_priority_4 = sorted_netto[len(netto) - idx_percentile]

    # find timesteps relevant for simulation and discard remaining
    idx_start = (s.start_time.replace(tzinfo=None) - nsm_start_time) // s.interval
    idx_start = idx_start if 0 < idx_start < len(netto) else 0
    idx_end = min(idx_start + s.n_intervals, len(netto))
    netto = netto[idx_start:idx_end]
    curtailment = curtailment[idx_start:idx_end]

    # zero-pad for same length as scenario
    netto += [0]*(s.n_intervals - len(netto))
    curtailment += [0]*(s.n_intervals - len(curtailment))

    # set priorities (default: 4)
    priorities = [4]*s.n_intervals
    for t in range(s.n_intervals):
        if curtailment[t] > 0:
            # highest priority: curtailment (must be capped)
            priorities[t] = 1
        elif netto[t] < cutoff_priority_1:
            # percentile with smallest load
            priorities[t] = 1
        elif netto[t] > cutoff_priority_4:
            # percentile with largest load
            priorities[t] = 4
        elif netto[t] < 0:
            # not in smallest or largest percentile but negative load
            priorities[t] = 2
        elif netto[t] >= 0:
            # not in smallest or largest percentile but positive load
            priorities[t] = 3

    # default schedule: just basic power needs
    schedule = [v for v in flex["base"]]

    def distribute_energy_balanced(period, energy_needed, priority_selection):
        """Distributes energy across a time period, prefering certain priorities over others.
        The algorithm tries to distribute needed energy across preferred priority timesteps
        and only if that is insufficient the time steps of the second most preferred priority
        are taken into account and so on.
        Schedule is raised if we want to allow customer to charge more during this period.
        Otherwise the schedule is lowered.

        :param period: List of timestep indicies of the period the energy is distributed to
        :type period: list
        :param charge_period: Determines whether schedule should be raised or lowered.
        "type charge_period: bool
        :param energy_needed: Amount of energy to be distributed.
        :type energy_needed: float
        :param priority_selection: List of priorities ordered by preference starting with most
                                   preferred.
        :type priority_selection: list
        :return: total change in stored energy after distribution completes
        """

        power_needed = energy_needed * ts_per_hour
        energy_distributed = 0
        for priority in priority_selection:

            if power_needed < EPS:
                # all vehicles charged
                break

            # count timesteps with current priority in interval
            priority_timesteps = 0
            for time in period:
                if priorities[time] == priority:
                    priority_timesteps += 1

            # distribute remaining power needed over priority timesteps
            saturated = 0
            while priority_timesteps > saturated and power_needed > EPS:
                power_per_ts = power_needed / (priority_timesteps - saturated)
                saturated = 0
                for time in period:
                    if priorities[time] == priority:
                        # calculate amount of power that can still be charged
                        if charge_period:
                            flexibility = flex["max"][time] - schedule[time]
                        else:
                            flexibility = schedule[time] - flex["min"][time]
                        power = min(power_per_ts, flexibility)
                        if power < EPS:
                            # schedule at limit: can't charge
                            saturated += 1
                        else:
                            # power fits here: increase schedule, decrease power needed
                            if charge_period:
                                schedule[time] += power
                                energy_distributed += \
                                    (power * flex["vehicles"]["efficiency"]) / ts_per_hour
                            else:
                                schedule[time] -= power * flex["vehicles"]["efficiency"]
                                energy_distributed -= power / ts_per_hour
                            power_needed -= power

        return energy_distributed

    for interval in flex["intervals"]:
        # loop until all needs satisfied
        capacity = flex["vehicles"]["capacity"]
        energy_stored = flex["vehicles"]["desired_energy"] - interval["needed"]

        # brake up interval into charging (prio 1,2) and discharging (prio 3,4) periods
        periods = [[]]
        if flex["vehicles"]["v2g"]:
            prev_prio = priorities[interval["time"][0]]
            for time in interval["time"]:
                priority = priorities[time]
                if (all([p <= 2 for p in [prev_prio, priority]]) or
                        all([p > 2 for p in [prev_prio, priority]])):
                    periods[-1].append(time)
                else:
                    periods.append([time])
                prev_prio = priority
        else:
            periods = [interval["time"]]

        # go through periods chronologically
        # FIRST determine energy goal of each period based on priority
        # Then raise/lower schedule in a balanced across period to reach that goal
        for i, period in enumerate(periods, start=1):
            desired_energy_stored = flex["vehicles"]["desired_energy"]
            if flex["vehicles"]["v2g"]:
                charge_period = priorities[period[0]] <= 2
                last_period = (i == len(periods))
                if charge_period:
                    priority_selection = [1, 2]
                    if not last_period:
                        desired_energy_stored = capacity
                else:
                    priority_selection = [4, 3]
                    if not last_period:
                        desired_energy_stored = flex["vehicles"]["discharge_limit"] * capacity
            else:
                charge_period = True
                priority_selection = [1, 2, 3, 4]

            energy_needed = desired_energy_stored - energy_stored

            if not charge_period:
                energy_needed *= -1

            energy_stored += distribute_energy_balanced(period, energy_needed, priority_selection)

        # if at the end of the charging interval vehicles are not charged to desired SOC
        # go through all periods again, this time from latest to earliest and raise the schedule
        # as much as possible until enough energy is allocated to charge vehicles to desired SOC
        charge_period = True
        priority_selection = [1, 2, 3, 4]
        for period in reversed(periods):
            if energy_stored >= desired_energy_stored:
                break
            energy_needed = desired_energy_stored - energy_stored
            energy_stored += distribute_energy_balanced(period, energy_needed, priority_selection)

    # create schedule for batteries
    batteries = flex["batteries"]  # members: stored, power, free

    # find periods of same priority
    t_start = 0
    t_end = 0
    while t_end < len(priorities):
        if priorities[t_end] != priorities[t_start]:
            # different priority started
            duration = t_end - t_start
            # (dis)charge depending on priority
            if priorities[t_start] <= 2:
                # prio 1/2: charge
                energy = batteries["free"] / batteries["efficiency"]
            else:
                # prio 3/4: discharge
                energy = -batteries["stored"] * batteries["efficiency"]
            # distribute energy over period of same priority
            for t in range(t_start, t_end):
                t_left = duration - (t - t_start)
                if energy > 0:
                    # charge
                    e = min(
                        batteries["power"] / ts_per_hour,
                        energy / t_left,
                        (flex["max"][t] - schedule[t]) / ts_per_hour)
                    e_bat_change = e * batteries["efficiency"]
                else:
                    # discharge
                    e = -min(
                        (batteries["power"] * batteries["efficiency"]) / ts_per_hour,
                        -energy / t_left,
                        (schedule[t] - flex["min"][t]) / ts_per_hour)
                    e_bat_change = e / batteries["efficiency"]
                batteries["stored"] += e_bat_change
                batteries["free"] -= e_bat_change
                schedule[t] += e * ts_per_hour
                energy -= e
                assert batteries["stored"] >= -EPS and batteries["free"] >= -EPS, (
                   "Battery fail: negative energy")
                assert flex["min"][t] - EPS <= schedule[t] <= flex["max"][t] + EPS, (
                    "{}: schedule not within flexibility".format(t))
            # keep track of next period
            t_start = t_end
        # search end of priority
        t_end += 1

    args.output = args.output or '.'.join(args.scenario.split('.')[:-1]) + "_schedule.csv"
    print("Writing to", args.output)
    # write schedule to file
    with open(args.output, 'w') as f:
        # header
        f.write("timestamp, schedule [kW], charge\n")
        cur_time = s.start_time - s.interval
        for t in range(s.n_intervals):
            cur_time += s.interval
            f.write("{}, {}, {}\n".format(
                cur_time.isoformat(), schedule[t], int(priorities[t] <= 2)))

    # add schedule file info to scenario JSON
    scenario_json['events']['schedule_from_csv'] = {
        'column': 'schedule [kW]',
        'start_time': s.start_time.isoformat(),
        'step_duration_s': s.interval.seconds,
        'csv_file': os.path.relpath(args.output, os.path.dirname(args.scenario)),
        'grid_connector_id': list(s.constants.grid_connectors.keys())[0]
    }
    scenario_json['scenario']['core_standing_time'] = args.core_standing_time
    with open(args.scenario, 'w') as f:
        json.dump(scenario_json, f, indent=2)

    if args.visual:
        # plot flex with schedule, input and priorities
        import matplotlib.pyplot as plt

        fig, axes = plt.subplots(3, 1)
        # plot flex and schedule
        axes[0].step(
            range(s.n_intervals),
            list(zip(flex["min"], flex["base"], flex["max"], schedule)),
            label=["min", "base", "max", "schedule"])
        axes[0].axhline(color='k', linestyle='--', linewidth=1)
        axes[0].set_xlim([0, s.n_intervals])
        axes[0].legend()
        axes[0].set_ylabel("power [kW]")
        # plot input file
        axes[1].step(
            range(s.n_intervals),
            list(zip(netto, curtailment)),
            label=["netto", "curtailment"])
        axes[1].legend()
        axes[1].set_xlim([0, s.n_intervals])
        axes[1].set_ylabel("power [kW]")
        # plot priorities
        axes[2].step(
            range(s.n_intervals),
            priorities,
            label="priorities")
        axes[2].legend()
        axes[2].set_xlim([0, s.n_intervals])
        axes[2].set_yticks([1, 2, 3, 4])
        plt.show()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Generate a schedule for a scenario.')
    parser.add_argument('scenario', nargs='?', help='Scenario input file')
    parser.add_argument('--input',
                        help='Timeseries with power and limit. '
                        'Columns: curtailment, netto (timestamp ignored)')
    parser.add_argument('--output', '-o',
                        help='Specify schedule file name, '
                        'defaults to <scenario>_schedule.csv')
    parser.add_argument('--priority-percentile', default=0.25, type=float,
                        help='Percentiles for priority determination')
    parser.add_argument('--core-standing-time', default=None,
                        help='Define time frames as well as full '
                        'days during which the fleet is guaranteed to be available in a JSON '
                        'obj like: {"times":[{"start": [22,0], "end":[1,0]}], "full_days":[7]}')
    parser.add_argument('--visual', '-v', action='store_true', help='Plot flexibility and schedule')
    parser.add_argument('--config', help='Use config file to set arguments')

    args = parser.parse_args()

    # parse JSON obj for core standing time if supplied via cmd line
    try:
        args.core_standing_time = json.loads(args.core_standing_time)
    except JSONDecodeError:
        args.core_standing_time = None
        warnings.warn('Value for core standing time could not be parsed and is omitted.')
    except TypeError:
        # no core standing time provided, defaulted to None
        pass

    util.set_options_from_config(args, check=True, verbose=False)

    missing = [arg for arg in ["scenario", "input"] if vars(args).get(arg) is None]
    if missing:
        raise SystemExit("The following arguments are required: {}".format(", ".join(missing)))

    generate_schedule(args)
