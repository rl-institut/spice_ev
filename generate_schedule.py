#!/usr/bin/env python3

import argparse
import csv
import json
import os

from src import scenario, strategy, util

PRIO_BELOW_MAX = 0.9  # try to stay below 90% of max load
EPS = 1e-8


def generate_flex_band(scenario):
    # generate flexibility potential with perfect foresight

    # generate basic strategy
    s = strategy.Strategy(
        scenario.constants, scenario.start_time, **{"interval": scenario.interval})
    event_steps = scenario.events.get_event_steps(
        scenario.start_time, scenario.n_intervals, scenario.interval)
    gc = list(scenario.constants.grid_connectors.values())[0]

    def clamp_to_gc(power):
        # helper function: make sure to stay within GC power limits
        return min(max(power, -gc.max_power), gc.max_power)

    cars = {vid: [0, 0, 0] for vid in s.world_state.vehicles}
    flex = {
        "min": [],
        "base": [],
        "max": [],
        "batteries": {
            "stored": 0,
            "power": 0,
            "free": 0,  # how much can still be stored?
        },
        "intervals": [],
    }

    # get battery info: how much can be discharged in beginning, how much if fully charged?
    batteries = s.world_state.batteries.values()
    bat_init_discharge_power = sum([b.get_available_power(s.interval) for b in batteries])
    for b in batteries:
        if b.capacity > 2**30:
            print("WARNING: battery without capacity detected")
        flex["batteries"]["stored"] += b.soc * b.capacity
        flex["batteries"]["power"] += b.loading_curve.max_power
        flex["batteries"]["free"] += (1 - b.soc) * b.capacity
        b.soc = 1
    bat_full_discharge_power = sum([b.get_available_power(s.interval) for b in batteries])

    vehicles_present = False
    power_needed = 0

    for step_i in range(scenario.n_intervals):
        s.step(event_steps[step_i])

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
                    vehicle_power_needed = max(v.get_delta_soc(), 0) * v.battery.capacity
                    v.battery.soc = v.desired_soc
                    v2g = v.battery.get_available_power(s.interval) if v.vehicle_type.v2g else 0
                    cars[vid] = [charging_power, vehicle_power_needed, v2g]

        pv_support = max(-base_flex, 0)
        if num_cars_present:
            # PV surplus can support vehicle charging
            for v in cars.values():
                if pv_support <= EPS:
                    break
                power = min(v[1], pv_support)
                v[1] -= power
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
            info["time"].append(step_i)
        else:
            # all vehicles left
            if vehicles_present:
                # first TS with all vehicles left: update power needed
                flex["intervals"][-1]["needed"] = power_needed
            vehicle_flex = power_needed = v2g_flex = 0
        vehicles_present = num_cars_present > 0

        battery_flex = bat_init_discharge_power if step_i == 0 else bat_full_discharge_power
        # PV surplus can also feed batteries
        pv_to_battery = min(battery_flex, pv_support)
        battery_flex -= pv_to_battery
        pv_support -= pv_to_battery
        base_flex += pv_to_battery

        flex["base"].append(clamp_to_gc(base_flex))
        # min: no vehicle charging, discharge from batteries and V2G
        flex["min"].append(clamp_to_gc(base_flex - battery_flex - v2g_flex))
        # max: all vehicle and batteries charging
        flex["max"].append(clamp_to_gc(base_flex + vehicle_flex + battery_flex))

    return flex


def generate_schedule(args):

    # read in scenario
    with open(args.scenario, 'r') as f:
        scenario_json = json.load(f)
        scenario_json['events']['schedule_from_csv'] = {}
        s = scenario.Scenario(scenario_json, os.path.dirname(args.scenario))

    assert len(s.constants.grid_connectors) == 1, "Only one grid connector supported"

    # compute flexibility potential (min/max) for each timestep
    flex = generate_flex_band(s)

    brutto = []
    surplus = []
    with open(args.input, 'r', newline='') as f:
        reader = csv.DictReader(f)
        for row_idx, row in enumerate(reader):
            if row_idx >= s.n_intervals:
                break
            brutto.append(-float(row["brutto"]))
            surplus.append(-float(row["abregelung"]))
    # zero-pad for same length as scenario
    brutto += [0]*(s.n_intervals - len(brutto))
    surplus += [0]*(s.n_intervals - len(surplus))
    max_load = max(brutto)

    # set priorities (default: 4)
    priorities = [4]*s.n_intervals
    for t in range(s.n_intervals):
        if surplus[t] > 0:
            # highest priority: surplus (must be capped)
            priorities[t] = 1
        elif brutto[t] > PRIO_BELOW_MAX * max_load:
            # don't charge if load close to max load
            priorities[t] = 2
        elif brutto[t] < 0:
            # charge if load negative (feed-in)
            priorities[t] = 3

    # default schedule: just basic power needs
    schedule = [v for v in flex["base"]]

    for interval in flex["intervals"]:
        # loop until all needs satisfied
        power_needed = interval["needed"]

        for priority in [2, 4]:
            # power close to max or default: discharge V2G
            for time in interval["time"]:
                if priorities[time] == priority:
                    # calculate power for V2G
                    power = schedule[time] - flex["min"][time] - flex["batteries"]["power"]
                    # discharge
                    schedule[time] -= power
                    power_needed += power

        for priority in [1, 3, 4]:
            # surplus or default: take power from grid and make sure vehicles are charged
            if power_needed < EPS:
                # all vehicles charged
                break

            # count timesteps with current priority in interval
            priority_timesteps = 0
            for time in interval["time"]:
                if priorities[time] == priority:
                    priority_timesteps += 1

            # distribute remaining power needed over priority timesteps
            saturated = 0
            while priority_timesteps > saturated and power_needed > EPS:
                power_per_ts = power_needed / (priority_timesteps - saturated)
                saturated = 0
                for time in interval["time"]:
                    if priorities[time] == priority:
                        # calculate amount of power that can still be charged
                        power = min(power_per_ts, flex["max"][time] - schedule[time])
                        if power < EPS:
                            # schedule at limit: can't charge
                            saturated += 1
                        else:
                            # power fits here: increase schedule, decrease power needed
                            schedule[time] += power
                            power_needed -= power

    # create schedule for batteries
    batteries = flex["batteries"]  # members: stored, power, free
    if batteries:
        for t in range(s.n_intervals):
            if priorities[t] % 2:
                # prio 1/3: charge
                power = min(batteries["power"], batteries["free"], flex["max"][t] - schedule[t])
                batteries["stored"] += power
                batteries["free"] -= power
                schedule[t] += power
            else:
                # prio 2/4: discharge
                power = min(batteries["power"], batteries["stored"], schedule[t] - flex["min"][t])
                batteries["stored"] -= power
                batteries["free"] += power
                schedule[t] -= power

    args.output = args.output or '.'.join(args.scenario.split('.')[:-1]) + "_schedule.csv"
    print("Writing to", args.output)
    # write schedule to file
    with open(args.output, 'w') as f:
        # header
        f.write("timestamp, schedule [kW]\n")
        cur_time = s.start_time - s.interval
        for t in range(s.n_intervals):
            cur_time += s.interval
            f.write("{}, {}\n".format(cur_time.isoformat(), schedule[t]))

    # add schedule file info to scenario JSON
    scenario_json['events']['schedule_from_csv'] = {
        'column': 'schedule [kW]',
        'start_time': s.start_time.isoformat(),
        'step_duration_s': s.interval.seconds,
        'csv_file': os.path.relpath(args.output, os.path.dirname(args.scenario)),
        'grid_connector_id': list(s.constants.grid_connectors.keys())[0]
    }
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
            list(zip(brutto, surplus)),
            label=["brutto", "surplus"])
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
        plt.show()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Generate a schedule for a scenario.')
    parser.add_argument('scenario', nargs='?', help='Scenario input file')
    parser.add_argument('--input',
                        help='Timeseries with power and limit. '
                        'Columns: abregelung, brutto (timestamp ignored)')
    parser.add_argument('--output', '-o',
                        help='Specify schedule file name, '
                        'defaults to <scenario>_schedule.csv')
    parser.add_argument('--max_load_range', default=0.1,
                        help='Area around max_load that should be discouraged')
    parser.add_argument('--flexibility_per_car', default=16, help='Flexibility of each car in kWh')
    parser.add_argument('--start_time', default='20:00:00', help='Start time of flexibility window')
    parser.add_argument('--end_time', default='05:45:00', help='End time of flexibility window')
    parser.add_argument('--visual', '-v', action='store_true', help='Plot flexibility and schedule')
    parser.add_argument('--config', help='Use config file to set arguments')

    args = parser.parse_args()
    util.set_options_from_config(args, check=True, verbose=False)

    missing = [arg for arg in ["scenario", "input"] if vars(args).get(arg) is None]
    if missing:
        raise SystemExit("The following arguments are required: {}".format(", ".join(missing)))

    generate_schedule(args)
