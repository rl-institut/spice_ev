from copy import deepcopy
from datetime import timedelta
import json
import logging
from time import perf_counter

import oemof.solph as solph
import pandas as pd
import pyomo.environ as po

from spice_ev.events import FixedLoad, GridOperatorSignal, LocalEnergyGeneration
from spice_ev.oemof_objective import get_gc_rates, read_objective
from spice_ev.strategy import Strategy


class OemofSolph(Strategy):
    """ Create and solve energy system with oemof, then follow the optimal SoC. """
    def __init__(self, components, start_time, **kwargs):
        t_start = perf_counter()
        # desired SoC at departure is a soft constraint, shortfall is penalized (per kWh):
        # at least DESIRED_SOC_PENALTY, at least FACTOR times the highest saving per kWh
        self.FORCE_DESIRED_SOC = True
        self.DESIRED_SOC_PENALTY = 10
        self.DESIRED_SOC_PENALTY_FACTOR = 2
        # fallback cost of peak power per GC (€/kW·a, not annualized), avoids arbitrary peaks
        # at GCs without other per-kW cost term
        self.PEAK_POWER_COST = 1
        self.SOLVER = "cbc"
        super().__init__(components, start_time, **kwargs)
        self.description = "oemof"
        # opportunity charging not supported (charging at unknown stations is not modeled)
        if any(cs_id.split("_")[-1] == "opps" for cs_id in self.world_state.charging_stations):
            raise Exception("Opportunity stations are defined in electrified stations. The "
                            "oemof-solph strategy is only valid for depot stations. "
                            "Simulation aborted.")
        # objective settings per GC (see oemof_objective.json)
        gc_settings = read_objective(
            kwargs.get("oemof_objective"), self.world_state.grid_connectors.keys())
        for gc_id, settings in gc_settings.items():
            if settings["electricity_costs_method"]["value"].startswith("variable"):
                raise NotImplementedError(
                    f"oemof_objective: GC {gc_id}: electricity_costs_method "
                    f"{settings['electricity_costs_method']['value']} not supported yet")
        # cost parameter file: -cp in SpiceEV, cost_parameters_path in SimBA
        price_sheet_path = kwargs.get("cost_parameters_file") or kwargs.get(
            "cost_parameters_path")
        price_sheet = dict()
        if price_sheet_path is not None:
            with open(price_sheet_path) as f:
                price_sheet = json.load(f)
        elif any(s["energy_charges"]["weight"] > 0 for s in gc_settings.values()):
            raise Exception("oemof_solph needs a cost parameter file (cost_parameters_file)")
        # needed in step to get index of precomputed SoC
        self.start_time = start_time
        # precomputed SoC per battery and time step
        self.batteries = {"stationary": dict(), "vehicle": dict()}

        n_intervals = kwargs["n_intervals"]
        if n_intervals == 0:
            return

        time_index = pd.date_range(
            start=start_time,
            end=kwargs["stop_time"],
            freq=kwargs["interval"],
            inclusive="both",
        )
        assert len(time_index) - 1 == n_intervals, (
            f"Length mismatch: index {len(time_index)} vs n_intervals {n_intervals}")

        def time_to_index(datetime):
            return -((start_time - datetime) // self.interval)

        # get GC info for whole simulation from events
        # (energy prices come from the cost parameter file, not from signals)
        gc_update = {gc_id: {
            "max_power": gc.max_power,
            "scalar": True,
            "fixed_load": dict(),
            "generation": dict(),
        } for gc_id, gc in self.world_state.grid_connectors.items()}
        gc_events = kwargs["events"].grid_operator_signals
        for event in sorted(gc_events, key=lambda ev: ev.start_time):
            if type(event) is not GridOperatorSignal:
                # ignore schedule (type VehicleEvent)
                continue
            gc_id = event.grid_connector_id
            if gc_id not in gc_update:
                # GC not part of this strategy's world state
                continue
            # events before scenario start apply from the first time step
            idx = max(time_to_index(event.start_time), 0)
            # set max power from event, never above static GC max power (as in Strategy.step)
            event_power = event.max_power
            static_power = self.world_state.grid_connectors[gc_id].max_power
            if event_power is not None and static_power is not None:
                event_power = min(event_power, static_power)
            gc_power = gc_update[gc_id]["max_power"]
            if gc_power is not None:
                if event_power is not None:
                    if not gc_update[gc_id]["scalar"]:
                        gc_power.iloc[idx:] = event_power
                    elif gc_power != event_power:
                        # was scalar, but changed: change to list
                        gc_power = pd.Series(gc_power, index=time_index)
                        gc_power.iloc[idx:] = event_power
                        gc_update[gc_id]["max_power"] = gc_power
                        gc_update[gc_id]["scalar"] = False
                    # scalar and unchanged: nothing to do
            elif event_power is not None:
                # connector power not set before
                gc_power = pd.Series(1e9, index=time_index)
                gc_power.iloc[idx:] = event_power
                gc_update[gc_id]["max_power"] = gc_power
                gc_update[gc_id]["scalar"] = False

        # fixed loads
        for name, load_list in kwargs["events"].fixed_load_lists.items():
            gc_id = load_list.grid_connector_id
            if gc_id not in gc_update:
                continue
            series = pd.Series(0.0, index=time_index)
            for event in load_list.get_events(name, FixedLoad):
                idx = max(time_to_index(event.start_time), 0)
                series.iloc[idx:] = event.value
            gc_update[gc_id]["fixed_load"][name] = series

        # fixed generation
        for name, gen_list in kwargs["events"].local_generation_lists.items():
            gc_id = gen_list.grid_connector_id
            if gc_id not in gc_update:
                continue
            series = pd.Series(0.0, index=time_index)
            for event in gen_list.get_events(name, LocalEnergyGeneration):
                idx = max(time_to_index(event.start_time), 0)
                series.iloc[idx:] = event.value
            gc_update[gc_id]["generation"][name] = series

        # vehicle info: when driving (with which power), when connected to which CS
        vehicle_models = dict()
        # CS -> Vehicle -> connection info
        cs_models = dict()
        for v_id, vehicle in self.world_state.vehicles.items():
            vehicle_models[v_id] = {
                "spent": 0,  # SoC delta since last charge
                # ("drive", SoC delta, arrival time) / ("charge", start, end, CS ID)
                "sequence": [],
                "desired": pd.Series(0.0, index=time_index),  # desired SoC at departure
                "driving": pd.Series(0.0, index=time_index),  # energy drain while driving
                "charging": pd.Series(0, index=time_index),  # mark when connected
                "connected": vehicle.connected_charging_station,  # current CS
                "last_desired": (
                    vehicle.desired_soc
                    if vehicle.connected_charging_station and self.FORCE_DESIRED_SOC
                    else 0
                ),
                # last arrival / departure index
                "last_arrival": 0 if vehicle.connected_charging_station else None,
                "last_departure": None if vehicle.connected_charging_station else 0,
            }
            cs_id = vehicle.connected_charging_station
            if cs_id is None:
                continue
            cs_models.setdefault(cs_id, dict())[v_id] = pd.Series(0, index=time_index)

        # process driving events
        # vehicle ID -> time of first arrival with negative SoC (when starting fully charged)
        infeasible = dict()
        # GC ID -> energy charged by vehicles in simulated period (kWh, from grid)
        charged_energy = {gc_id: 0 for gc_id in gc_update}
        vehicle_events = kwargs["events"].vehicle_events
        for event in sorted(vehicle_events, key=lambda ev: ev.start_time):
            v_id = event.vehicle_id
            vehicle = self.world_state.vehicles.get(v_id)
            if vehicle is None:
                # unknown vehicle
                continue
            v_info = vehicle_models[v_id]
            if event.event_type == "arrival":
                cs_id = event.update.get("connected_charging_station")
                v_info["spent"] -= event.update.get("soc_delta", 0)
                if v_info["spent"] > 1 + self.EPS:
                    # more than a full battery used since last charge: negative SoC
                    infeasible.setdefault(v_id, event.start_time)
                if cs_id is None or cs_id not in self.world_state.charging_stations:
                    # not connected: treat as still driving
                    continue
                start_idx = time_to_index(event.start_time)
                if start_idx > 0 and start_idx == v_info["last_departure"]:
                    # arrival in same time step as last departure: no time step for driving
                    etd = event.update.get("estimated_time_of_departure")
                    if etd is not None and time_to_index(etd) <= start_idx + 1:
                        # too short to drive and charge: treat as still driving
                        # (consumption carried over to next arrival)
                        continue
                    # use first time step of standing time for driving
                    start_idx += 1
                cs_models.setdefault(cs_id, dict()).setdefault(
                    v_id, pd.Series(0, index=time_index))
                v_info["sequence"].append(("drive", v_info["spent"], event.start_time))
                if start_idx > 0:
                    # start_idx = 0: charging begins at scenario start -> no prior driving
                    last_departure = v_info["last_departure"]
                    if last_departure is None:
                        raise Exception(
                            f"{v_id}: arrival without departure {event.start_time.isoformat()}")
                    # steps > 0 guaranteed by same-time-step check above
                    steps = start_idx - last_departure
                    energy = v_info["spent"] * vehicle.battery.capacity * vehicle.battery.efficiency
                    avg_power = energy / steps * self.ts_per_hour
                    if any(v_info["driving"].iloc[last_departure:start_idx] > 0):
                        raise Exception(f"Vehicle {v_id}: multiple driving at same time")
                    v_info["driving"].iloc[last_departure:start_idx] = avg_power
                v_info["last_desired"] = (
                    event.update["desired_soc"] if self.FORCE_DESIRED_SOC else 0)
                v_info["connected"] = cs_id
                v_info["last_arrival"] = start_idx
                v_info["last_departure"] = None
                v_info["last_etd"] = event.update.get("estimated_time_of_departure")
                # energy to recharge at this GC (annual energy estimate, simulated period only)
                if start_idx < n_intervals:
                    gc_id = self.world_state.charging_stations[cs_id].parent
                    charged_energy[gc_id] += (
                        v_info["spent"] * vehicle.battery.capacity / vehicle.battery.efficiency)
                v_info["spent"] = 0
            elif event.event_type == "departure":
                cs_id = v_info["connected"]
                if cs_id is None:
                    # departure while not connected: no update (still driving)
                    continue
                start_idx = v_info["last_arrival"]
                if start_idx is None:
                    raise Exception(
                        f"{v_id}: departure without arrival {event.start_time.isoformat()}")
                end_idx = time_to_index(event.start_time)
                if end_idx < n_intervals:
                    # only set desired SoC if charging is finished before end of simulation
                    v_info["desired"].iloc[end_idx] = v_info["last_desired"]
                # mark times of charging with 1
                # add Series to notice double charges
                charging = pd.Series(0, index=time_index)
                charging.iloc[start_idx:end_idx] = 1
                v_info["charging"] += charging
                cs_models[cs_id][v_id] += charging
                v_info["sequence"].append(("charge", start_idx, end_idx, cs_id))
                v_info["last_arrival"] = None
                v_info["last_departure"] = end_idx
                v_info["connected"] = None

        if infeasible:
            # oemof storage can't go below zero: abort instead of returning a wrong plan
            details = ", ".join(f"{v_id} ({t.isoformat()})" for v_id, t in infeasible.items())
            logging.error(
                f"oemof-solph: {len(infeasible)} vehicle(s) reach negative SoC even when "
                f"starting fully charged: {details}")
            raise Exception("oemof-solph does not allow for negative SoC, scenario not feasible")

        for v_id, v_info in vehicle_models.items():
            # handle last interval
            last_arrival = v_info["last_arrival"]
            if last_arrival is not None:
                # still charging
                charging = pd.Series(0, index=time_index)
                charging.iloc[last_arrival:] = 1
                v_info["charging"] += charging
                cs_models[v_info["connected"]][v_id] += charging
                v_info["sequence"].append(
                    ("charge", last_arrival, n_intervals, v_info["connected"]))
                # desired SoC at estimated departure, at the latest at end of simulation
                etd = v_info.get("last_etd")
                end_idx = n_intervals if etd is None else min(time_to_index(etd), n_intervals)
                v_info["desired"].iloc[max(end_idx, last_arrival)] = v_info["last_desired"]
            # sanity checks
            if any(v_info["charging"] > 1):
                raise Exception(f"Vehicle {v_id}: multiple charging at same time")
            if any((v_info["charging"] > 0) & (v_info["driving"] > 0)):
                raise Exception(f"Vehicle {v_id}: charging and driving at same time")
        for cs_id, cs_v_info in cs_models.items():
            if any(sum(cs_v_info.values()) > 1):
                raise Exception(f"CS {cs_id}: multiple charging at same time")

        # each vehicle on its own must avoid negative SoC when charging with full CS power in
        # every standing time (GC limits ignored), otherwise the model is infeasible
        too_short = dict()
        for v_id, v_info in vehicle_models.items():
            vehicle = self.world_state.vehicles[v_id]
            battery = deepcopy(vehicle.battery)
            for step in v_info["sequence"]:
                if step[0] == "drive":
                    battery.soc -= step[1]
                    if battery.soc < -self.EPS:
                        too_short[v_id] = step[2]
                        break
                elif step[2] > step[1]:
                    cs_power = self.world_state.charging_stations[step[3]].max_power
                    battery.load(self.interval * (step[2] - step[1]), max_power=cs_power)
        if too_short:
            details = ", ".join(f"{v_id} ({t.isoformat()})" for v_id, t in too_short.items())
            logging.error(
                f"oemof-solph: {len(too_short)} vehicle(s) reach negative SoC even when charging "
                f"with full power whenever connected (standing time too short or initial SoC "
                f"too low): {details}")
            raise Exception("oemof-solph does not allow for negative SoC, scenario not feasible")

        # energy costs per GC: billing type from estimated annual energy, rates from cost file,
        # annualized and weighted (objective in €/a)
        annual_factor = timedelta(days=365) / (n_intervals * self.interval)
        for gc_id, info in gc_update.items():
            settings = gc_settings[gc_id]
            energy_sim = (
                charged_energy[gc_id]
                + sum(s.iloc[:-1].sum() for s in info["fixed_load"].values()) / self.ts_per_hour
                - sum(s.iloc[:-1].sum() for s in info["generation"].values()) / self.ts_per_hour)
            gc = self.world_state.grid_connectors[gc_id]
            weight = settings["energy_charges"]["weight"]
            rates = get_gc_rates(
                price_sheet, gc_id, gc.grid_operator, gc.voltage_level,
                energy_sim * annual_factor, weight)
            info["cost"] = rates["energy_price"] * annual_factor * weight
            logging.info(
                f"oemof: GC {gc_id}: method {settings['electricity_costs_method']['value']}, "
                f"grid operator {rates['grid_operator']}, voltage level {rates['voltage_level']}, "
                f"{rates['fee_type']} (estimated {energy_sim * annual_factor:.0f} kWh/a), "
                f"energy price {rates['energy_price']:.4f} €/kWh net, "
                f"weight {weight}, annual factor {annual_factor:.2f}")

        # split into independent energy systems: GCs are coupled only by vehicles visiting
        # several of them (union-find over GC IDs)
        gc_group = {gc_id: gc_id for gc_id in self.world_state.grid_connectors}

        def find_group(gc_id):
            while gc_group[gc_id] != gc_id:
                gc_id = gc_group[gc_id]
            return gc_id

        vehicle_gcs = dict()
        for cs_id, cs_v_info in cs_models.items():
            gc_id = self.world_state.charging_stations[cs_id].parent
            for v_id in cs_v_info:
                vehicle_gcs.setdefault(v_id, set()).add(gc_id)
        for gc_ids in vehicle_gcs.values():
            gc_ids = list(gc_ids)
            for gc_id in gc_ids[1:]:
                gc_group[find_group(gc_id)] = find_group(gc_ids[0])
        groups = dict()
        for gc_id in gc_group:
            groups.setdefault(find_group(gc_id), {
                "gcs": [], "vehicles": [], "batteries": []})["gcs"].append(gc_id)
        for v_id, gc_ids in vehicle_gcs.items():
            groups[find_group(next(iter(gc_ids)))]["vehicles"].append(v_id)
        for b_id, battery in self.world_state.batteries.items():
            groups[find_group(battery.parent)]["batteries"].append(b_id)

        # get interpolation points for vehicle type charging curves
        charging_curve_info = dict()
        for vt in self.world_state.vehicle_types.values():
            charging_curve_info[vt] = get_battery_parameters(vt.charging_curve)

        t_setup = perf_counter() - t_start
        t_solve = 0
        for group in groups.values():
            if not group["vehicles"] and not group["batteries"]:
                # nothing to optimize
                continue
            t_group = perf_counter()
            # technical terms fitting the objective in €/a (per group, see doc)
            energy_costs = [gc_update[gc_id]["cost"] for gc_id in group["gcs"]]
            # per-kW terms: only fallback peak cost so far, 1 kWh in one step = ts_per_hour kW
            c_kw = self.PEAK_POWER_COST
            penalty = max(self.DESIRED_SOC_PENALTY, self.DESIRED_SOC_PENALTY_FACTOR * max(
                cost + c_kw * self.ts_per_hour for cost in energy_costs))
            # cost of auxiliary flows (avoid cycle charging), relative to cheapest energy
            positive_costs = [cost for cost in energy_costs if cost > 0]
            eps = 1e-4 * (min(positive_costs) if positive_costs else 1)
            logging.info(
                f"oemof: GC(s) {group['gcs']}: desired SoC penalty {penalty:.2f} €/kWh, "
                f"auxiliary flow cost {eps:.2e} €/kWh")
            es = solph.EnergySystem(timeindex=time_index, infer_last_interval=False)

            # setup GC
            gcs = dict()
            gc_sources = dict()
            for gc_id in group["gcs"]:
                gc_bus = solph.Bus(label=f"GC_{gc_id}")
                gc_max_power = gc_update[gc_id]["max_power"]
                # unknown max power: unlimited
                gc_nominal = 1 if gc_max_power is not None else None
                gc_sources[gc_id] = solph.components.Source(
                    label=f"GC_{gc_id}_source",
                    outputs={gc_bus: solph.Flow(
                        nominal_capacity=gc_nominal,
                        maximum=gc_max_power,
                        variable_costs=gc_update[gc_id]["cost"],
                    )}
                )
                es.add(gc_sources[gc_id])
                es.add(solph.components.Sink(
                    label=f"GC_{gc_id}_sink",
                    inputs={gc_bus: solph.Flow(
                        nominal_capacity=gc_nominal,
                        maximum=gc_max_power,
                        variable_costs=0,  # no associated cost or gain for feed-in
                    )}
                ))
                for name, fixed_load_list in gc_update[gc_id]["fixed_load"].items():
                    es.add(solph.components.Sink(
                        label=f"GC_{gc_id}_{name}_sink",
                        inputs={gc_bus: solph.Flow(nominal_capacity=1, fix=fixed_load_list)}
                    ))
                for name, generation_list in gc_update[gc_id]["generation"].items():
                    es.add(solph.components.Source(
                        label=f"GC_{gc_id}_{name}_source",
                        outputs={gc_bus: solph.Flow(nominal_capacity=1, fix=generation_list)}
                    ))
                gcs[gc_id] = gc_bus
                es.add(gc_bus)

            # battery model -> (battery type, battery ID, capacity)
            storage_info = dict()
            # stationary batteries
            for b_id in group["batteries"]:
                battery = self.world_state.batteries[b_id]
                battery_parameters = get_battery_parameters(battery.loading_curve)
                battery_model = solph.components.GenericStorage(
                    label=f"Stationary_{b_id}",
                    nominal_capacity=battery.capacity,
                    initial_storage_level=battery.soc,
                    balanced=False,  # different SoC at begin and end of simulation allowed
                    inflow_conversion_factor=battery.efficiency,
                    outflow_conversion_factor=battery.efficiency,
                    # next two parameters are charging curve
                    constant_soc_until=battery_parameters["until"],
                    fraction_saturation_charging=battery_parameters["end_p"],
                    inputs={gcs[battery.parent]: solph.Flow(
                        nominal_capacity=battery_parameters["power"],
                        variable_costs=eps,  # avoid cycle charging
                    )},
                    outputs={gcs[battery.parent]: solph.Flow(
                        nominal_capacity=battery_parameters["power"],
                    )},
                )
                es.add(battery_model)
                storage_info[battery_model] = ("stationary", b_id, battery.capacity)

            # vehicles: bus with battery and driving sink
            street = solph.components.Sink(label="driving", inputs=dict())
            if group["vehicles"]:
                es.add(street)
            v_buses = dict()
            # (battery model, time index, desired SoC) for soft desired SoC constraints
            desired_socs = []
            for v_id in group["vehicles"]:
                v_info = vehicle_models[v_id]
                vehicle = self.world_state.vehicles[v_id]
                v_bus = solph.Bus(label=f"V_{v_id}")
                es.add(v_bus)
                v_buses[v_id] = v_bus
                street.inputs[v_bus] = solph.Flow(nominal_capacity=1, fix=v_info["driving"])
                charging_curve = charging_curve_info[vehicle.vehicle_type]
                battery_model = solph.components.GenericStorage(
                    label=f"Bat_{v_id}",
                    nominal_capacity=vehicle.battery.capacity,
                    initial_storage_level=max(vehicle.battery.soc, 0),
                    balanced=False,  # allow different initial and final SoC
                    inflow_conversion_factor=vehicle.battery.efficiency,
                    outflow_conversion_factor=vehicle.battery.efficiency,
                    # next two parameters are charging curve
                    constant_soc_until=charging_curve["until"],
                    fraction_saturation_charging=charging_curve["end_p"],
                    inputs={v_bus: solph.Flow(
                        nominal_capacity=charging_curve["power"],
                        variable_costs=eps,  # avoid cycle charging
                    )},
                    outputs={v_bus: solph.Flow(
                        # unlimited output for driving
                        nominal_capacity=None,
                        variable_costs=eps,
                    )},
                )
                es.add(battery_model)
                storage_info[battery_model] = ("vehicle", v_id, vehicle.battery.capacity)
                for t, desired_soc in v_info["desired"].items():
                    if desired_soc > 0:
                        desired_socs.append((
                            battery_model, time_index.get_loc(t),
                            desired_soc * vehicle.battery.capacity))

            # setup charging station connections between GC and vehicles
            for cs_id, cs_v_info in cs_models.items():
                cs = self.world_state.charging_stations[cs_id]
                if cs.parent not in gcs:
                    continue
                gc_bus = gcs[cs.parent]
                for v_id, cs_charging in cs_v_info.items():
                    v_bus = v_buses[v_id]
                    vehicle = self.world_state.vehicles[v_id]
                    if vehicle.vehicle_type.v2g:
                        # bidirectional connection, only while vehicle is present
                        conn = solph.components.Link(
                            label=f"Conn_{cs_id}_{v_id}",
                            inputs={
                                gc_bus: solph.Flow(variable_costs=eps),
                                v_bus: solph.Flow(
                                    nominal_capacity=cs.max_power,
                                    maximum=cs_charging,
                                    variable_costs=eps,
                                )},
                            outputs={
                                gc_bus: solph.Flow(variable_costs=eps),
                                v_bus: solph.Flow(
                                    nominal_capacity=cs.max_power,
                                    maximum=cs_charging,
                                    variable_costs=eps,
                                )},
                            conversion_factors={(v_bus, gc_bus): 1, (gc_bus, v_bus): 1},
                        )
                    else:
                        # charging only (fewer variables than Link), only while vehicle is present
                        conn = solph.components.Converter(
                            label=f"Conn_{cs_id}_{v_id}",
                            inputs={gc_bus: solph.Flow(
                                nominal_capacity=cs.max_power,
                                maximum=cs_charging,
                                variable_costs=eps,
                            )},
                            outputs={v_bus: solph.Flow()},
                        )
                    es.add(conn)

            # solve energy system model with soft desired SoC constraints
            # oemof refuses root logger level DEBUG (slows down pyomo), raise temporarily
            root_logger = logging.getLogger()
            log_level = root_logger.level
            root_logger.setLevel(max(log_level, logging.INFO))
            try:
                model = solph.Model(es)
                storage_content = model.GenericStorageBlock.storage_content
                model.desired_soc_shortfall = po.Var(
                    range(len(desired_socs)), within=po.NonNegativeReals)
                model.desired_soc_constraint = po.Constraint(
                    range(len(desired_socs)),
                    rule=lambda m, i: (
                        storage_content[desired_socs[i][0], desired_socs[i][1]]
                        + m.desired_soc_shortfall[i] >= desired_socs[i][2]))
                # peak power drawn from each GC
                model.gc_peak = po.Var(list(gcs.keys()), within=po.NonNegativeReals)
                model.gc_peak_constraint = po.Constraint(
                    list(gcs.keys()), model.TIMESTEPS,
                    rule=lambda m, gc_id, t: (
                        m.flow[gc_sources[gc_id], gcs[gc_id], t] <= m.gc_peak[gc_id]))
                objective = (
                    model.objective.expr
                    + penalty * sum(
                        model.desired_soc_shortfall[i] for i in range(len(desired_socs)))
                    + self.PEAK_POWER_COST * sum(model.gc_peak[gc_id] for gc_id in gcs))
                model.del_component("objective")
                model.objective = po.Objective(sense=po.minimize, expr=objective)
                t_solve_start = perf_counter()
                try:
                    model.solve(solver=self.SOLVER)
                except RuntimeError as e:
                    # each vehicle feasible on its own (checked above): GC power insufficient
                    logging.error(
                        f"oemof-solph: no feasible solution for GC(s) {group['gcs']}, "
                        f"grid connector power too low to charge all vehicles in time")
                    raise Exception(
                        "oemof-solph does not allow for negative SoC, scenario not feasible") from e
                t_solve += perf_counter() - t_solve_start
            finally:
                root_logger.setLevel(log_level)
            # don't keep results: oemof objects can't be pickled (SimBA pickles scenario)
            results = solph.Results(model)

            # save computed SoC
            for battery_model, values in results["storage_content"].items():
                battery_type, b_id, capacity = storage_info[battery_model]
                self.batteries[battery_type][b_id] = list(values / capacity)[1:]
            logging.debug(
                f"oemof: GC(s) {group['gcs']} with {len(group['vehicles'])} vehicles "
                f"solved in {perf_counter() - t_group:.1f} s")

        logging.info(
            f"oemof: setup {t_setup:.1f} s, model building and solving "
            f"{perf_counter() - t_start - t_setup:.1f} s (solver {t_solve:.1f} s)")

    def step(self):
        """ Charge or discharge batteries to reach precomputed SoC of current time step.

        :return: current time and commands of the charging stations
        :rtype: dict
        """
        charging_stations = dict()
        # reset charging station power (nothing charged yet in this time step)
        for cs in self.world_state.charging_stations.values():
            cs.current_power = 0
        # index from current time, not a step counter: step may be called
        # several times or not at all per timestep (e.g. as sub-strategy of distributed)
        idx = (self.current_time - self.start_time) // self.interval
        # read out precomputed SoC, charge as needed
        for v_id, vehicle in self.world_state.vehicles.items():
            cs_id = vehicle.connected_charging_station
            if cs_id is None:
                # not connected: can't charge
                continue
            planned_soc = self.batteries["vehicle"].get(v_id)
            if planned_soc is None:
                # vehicle never charges in model
                continue
            desired_soc = planned_soc[idx]
            if abs(vehicle.battery.soc - desired_soc) < self.EPS:
                # precomputed SoC already reached, no change needed
                continue
            cs = self.world_state.charging_stations[cs_id]
            gc = self.world_state.grid_connectors[cs.parent]
            if vehicle.battery.soc < desired_soc:
                # actual SoC may deviate from plan (e.g. negative SoC): respect GC limit
                max_power = cs.max_power
                if gc.cur_max_power is not None:
                    max_power = min(max_power, gc.cur_max_power - gc.get_current_load())
                if max_power < self.EPS:
                    continue
                p = vehicle.battery.load(
                    self.interval, max_power=max_power, target_soc=desired_soc)["avg_power"]
            elif vehicle.vehicle_type.v2g:
                p = -vehicle.battery.unload(
                    self.interval, max_power=cs.max_power, target_soc=desired_soc)["avg_power"]
            else:
                # SoC above plan (e.g. connection not modeled), but no V2G: don't discharge
                continue
            charging_stations[cs_id] = gc.add_load(cs_id, p)
            cs.current_power += p

        for b_id, battery in self.world_state.batteries.items():
            desired_soc = self.batteries["stationary"][b_id][idx]
            if abs(battery.soc - desired_soc) < self.EPS:
                # precomputed SoC already reached, no change needed
                continue
            gc = self.world_state.grid_connectors[battery.parent]
            if battery.soc < desired_soc:
                p = battery.load(self.interval, target_soc=desired_soc)["avg_power"]
            else:
                p = -battery.unload(self.interval, target_soc=desired_soc)["avg_power"]
            gc.add_load(b_id, p)

        return {'current_time': self.current_time, 'commands': charging_stations}


def get_battery_parameters(charging_curve) -> dict:
    """ Get characteristics of a charging curve as needed by oemof GenericStorage.

    The curve must be constant up to one break point and may decrease linearly afterwards.

    :param charging_curve: charging curve of battery or vehicle type
    :type charging_curve: spice_ev.loading_curve.LoadingCurve
    :raises Exception: if charging curve has an unsupported shape
    :return: constant charging power ("power"), SoC of break point ("until", None if constant)
        and fraction of charging power at full battery ("end_p", None if constant)
    :rtype: dict
    """
    constant_power = None
    constant_soc_until = None
    end_power = None
    for soc, power in charging_curve.points:
        if soc == 0:
            # begin of charging curve: init power
            constant_power = power
            constant_soc_until = 0
        elif soc < 1:
            # intermediate points: look for break in charging curve
            if power == constant_power:
                # power constant until here
                constant_soc_until = soc
            else:
                # power was already decreased, oemof allows only one break
                raise Exception("Power must remain constant until break in charging curve")
        elif soc == 1:
            # end of charging curve
            if power == constant_power:
                # constant charging curve
                constant_soc_until = None
                end_power = None
            elif power > constant_power:
                raise Exception("Power must decrease after break in charging curve")
            else:
                # linear reduction: fraction of charging power at 100%
                end_power = power / constant_power
    return {
        "power": constant_power,
        "until": constant_soc_until,
        "end_p": end_power,
    }
