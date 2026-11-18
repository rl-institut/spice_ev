import oemof.solph as solph
import pandas as pd
from spice_ev.events import FixedLoad, GridOperatorSignal, LocalEnergyGeneration
from spice_ev.strategy import Strategy

from time import perf_counter


class OemofSolph(Strategy):
    """ Create and solve energy system with oemof"""
    def __init__(self, components, start_time, **kwargs):
        t1 = perf_counter()
        self.FORCE_DESIRED_SOC = True
        super().__init__(components, start_time, **kwargs)
        self.description = "oemof"

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

        es = solph.EnergySystem(
            timeindex=time_index,
            infer_last_interval=False,
        )

        # get GC info for whole simulation from events
        gc_update = {gc_id: {
            "cost": pd.Series(gc.cost.get("value"), index=time_index[:-1]),
            "max_power": gc.max_power,
            "scalar": True,
            "fixed_load": dict(),
            "generation": dict(),
        } for gc_id, gc in self.world_state.grid_connectors.items()}
        gc_events = kwargs["events"].grid_operator_signals
        for event in sorted(gc_events, key=lambda ev: ev.start_time):
            if not isinstance(event, GridOperatorSignal):
                # ignore schedule (type VehicleEvent)
                continue
            gc_id = event.grid_connector_id
            idx = time_to_index(event.start_time)
            if event.cost is not None:
                if event.cost["type"] == "fixed":
                    gc_update[gc_id]["cost"][idx:] = event.cost["value"]
                elif event.cost["type"] == "polynomial" and len(event.cost["value"]) > 1:
                    # special case: polynomial with only fixed part
                    value = event.cost["value"][1]
                    if sum(event.cost["value"]) == value:
                        gc_update[gc_id]["cost"][idx:] = value
                    else:
                        raise Exception("Only fixed costs allowed")
                else:
                    # unknown cost type
                    raise Exception("Only fixed costs allowed")
            # set max power from event
            gc_power = gc_update[gc_id]["max_power"]
            if gc_power is not None:
                if event.max_power is not None:
                    if gc_update[gc_id]["scalar"] and gc_power != event.max_power:
                        # was scalar, but changed: change to list
                        gc_power = pd.Series(gc_power, index=time_index)
                        gc_power[idx:] = event.max_power
                        gc_update[gc_id]["max_power"] = gc_power
                        gc_update[gc_id]["scalar"] = False
                    else:
                        gc_power[idx:] = event.max_power
            else:
                # connector power not set
                gc_power = pd.Series(None, index=time_index)
                gc_power[idx:] = event.max_power
                gc_update[gc_id]["max_power"] = gc_power
                gc_update[gc_id]["scalar"] = False

        # fixed loads
        for name, load_list in kwargs["events"].fixed_load_lists.items():
            gc_id = load_list.grid_connector_id
            series = pd.Series(0.0, index=time_index)
            for event in load_list.get_events(name, FixedLoad):
                idx = time_to_index(event.start_time)
                series[idx:] = event.value
            gc_update[gc_id]["fixed_load"][name] = series

        # fixed generation
        for name, gen_list in kwargs["events"].local_generation_lists.items():
            gc_id = gen_list.grid_connector_id
            series = pd.Series(0.0, index=time_index)
            for event in gen_list.get_events(name, LocalEnergyGeneration):
                idx = time_to_index(event.start_time)
                series[idx:] = event.value
            gc_update[gc_id]["generation"][name] = series

        # setup GC
        gcs = dict()
        for gc_id in self.world_state.grid_connectors.keys():
            gc_bus = solph.Bus(label=f"GC_{gc_id}")
            es.add(solph.components.Source(
                label=f"GC_{gc_id}_source",
                outputs={gc_bus: solph.Flow(
                    nominal_capacity=1,
                    maximum=gc_update[gc_id]["max_power"],
                    variable_costs=gc_update[gc_id]["cost"],
                )}
            ))
            es.add(solph.components.Sink(
                label=f"GC_{gc_id}_sink",
                inputs={gc_bus: solph.Flow(
                    nominal_capacity=1,
                    maximum=gc_update[gc_id]["max_power"],
                    variable_costs=0,  # no associated cost or gain for feed-in
                )}
            ))
            for name, fixed_load_list in gc_update[gc_id]["fixed_load"].items():
                es.add(solph.components.Sink(
                    label=f"GC_{gc_id}_{name}_sink",
                    inputs={gc_bus: solph.Flow(
                        nominal_capacity=1,
                        fix=fixed_load_list,
                    )}
                ))
            for name, generation_list in gc_update[gc_id]["generation"].items():
                es.add(solph.components.Source(
                    label=f"GC_{gc_id}_{name}_source",
                    outputs={gc_bus: solph.Flow(
                        nominal_capacity=1,
                        fix=generation_list,
                    )}
                ))
            gcs[gc_id] = gc_bus
            es.add(gc_bus)

        # battery dict to store battery info: energy system model, bus and SoC
        batteries = {"stationary": dict(), "vehicle": dict()}
        # stationary batteries
        for bat_id, battery in self.world_state.batteries.items():
            battery_parameters = get_battery_parameters(battery.loading_curve)
            battery_model = solph.components.GenericStorage(
                label=f"Stationary_{bat_id}",
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
                    variable_costs=self.EPS,  # avoid cycle charging
                )},
                outputs={gcs[battery.parent]: solph.Flow(
                    nominal_capacity=battery_parameters["power"],
                )},
            )
            es.add(battery_model)
            batteries["stationary"][bat_id] = {
                "model": battery_model,
                "bus": gcs[battery.parent],
                "soc": None,  # set after model simulation
                "capacity": battery.capacity,
            }

        # get interpolation points for vehicle type charging curves
        charging_curve_info = dict()
        for vt in self.world_state.vehicle_types.values():
            charging_curve_info[vt] = get_battery_parameters(vt.charging_curve)

        vehicle_models = dict()
        # CS -> Vehicle -> Connection
        cs_models = dict()
        for v_id, vehicle in self.world_state.vehicles.items():
            v_bus = solph.Bus(label=f"V_{v_id}")
            es.add(v_bus)
            vehicle_models[v_id] = {
                "bus": v_bus,
                "spent": 0,  # SoC delta since last charge
                "desired": pd.Series(0.0, index=time_index),  # force desired SoC at departure
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
            cs_models[cs_id] = {
                v_id: {
                    "conn": None,  # set after knowing when to charge
                    "cs_charging": pd.Series(0, index=time_index),
                }
            }

        # process driving events
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
                if cs_id is None:
                    # not connected: treat as still driving
                    continue
                if cs_id not in cs_models:
                    cs_models[cs_id] = dict()
                if v_id not in cs_models[cs_id]:
                    cs_models[cs_id][v_id] = {
                        "conn": None,
                        "cs_charging": pd.Series(0, index=time_index),
                    }
                start_idx = time_to_index(event.start_time)
                if start_idx > 0:
                    # start_idx = 0: charging begins at scenario start -> no prior driving
                    last_departure = v_info["last_departure"]
                    if last_departure is None:
                        raise Exception(
                            f"{v_id}: arrival without departure {event.start_time.isoformat()}")
                    steps = start_idx - last_departure
                    energy = v_info["spent"] * vehicle.battery.capacity * vehicle.battery.efficiency
                    if steps == 0 and energy > self.EPS:
                        raise Exception("no time for driving")
                    avg_power = energy / steps * self.ts_per_hour
                    if any(v_info["driving"].iloc[last_departure:start_idx] > 0):
                        raise Exception(f"Vehicle {v_id}: multiple driving at same time")
                    v_info["driving"].iloc[last_departure:start_idx] = avg_power
                v_info["last_desired"] = (
                    event.update["desired_soc"] if self.FORCE_DESIRED_SOC else 0)
                v_info["connected"] = cs_id
                v_info["last_arrival"] = start_idx
                v_info["last_departure"] = None
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
                cs_info = cs_models[cs_id][v_id]
                cs_info["cs_charging"] += charging
                v_info["last_arrival"] = None
                v_info["last_departure"] = end_idx

        street = solph.components.Sink(label="driving", inputs=dict())
        es.add(street)

        for v_id, v_info in vehicle_models.items():
            # handle last interval
            last_arrival = v_info["last_arrival"]
            last_departure = v_info["last_departure"]
            if last_arrival:
                # still charging
                cs_id = v_info["connected"]
                charging = pd.Series(0, index=time_index)
                charging.iloc[last_arrival:] = 1
                v_info["charging"] += charging
                cs_models[cs_id][v_id]["cs_charging"] += charging

            is_charging = v_info["charging"] > 0
            is_driving = v_info["driving"] > 0
            if last_departure:
                # after last departure, driving is not defined (unknown required energy)
                is_charging = is_charging.iloc[:last_departure]
                is_driving = is_driving.iloc[:last_departure]

            # sanity checks
            if any(v_info["charging"] > 1):
                raise Exception(f"Vehicle {v_id}: multiple charging at same time")
            if any(is_charging == is_driving) and any(is_charging + is_driving):
                raise Exception(f"Vehicle {v_id}: charging and driving at same time")

            vehicle = self.world_state.vehicles[v_id]
            street.inputs[v_info["bus"]] = solph.Flow(
                nominal_capacity=1,
                fix=v_info["driving"],
            )

            charging_curve = charging_curve_info[vehicle.vehicle_type]
            battery_model = solph.components.GenericStorage(
                label=f"Bat_{v_id}",
                nominal_capacity=vehicle.battery.capacity,
                initial_storage_level=vehicle.battery.soc,
                balanced=False,  # allow different initial and final SoC
                min_storage_level=v_info["desired"],  # force desired SoC to be reached on departure
                inflow_conversion_factor=vehicle.battery.efficiency,
                outflow_conversion_factor=vehicle.battery.efficiency,
                # next two parameters are charging curve
                constant_soc_until=charging_curve["until"],
                fraction_saturation_charging=charging_curve["end_p"],
                inputs={v_info["bus"]: solph.Flow(
                    nominal_capacity=charging_curve["power"],
                    variable_costs=self.EPS,  # avoid cycle charging
                )},
                outputs={v_info["bus"]: solph.Flow(
                    # unlimited output for driving
                    nominal_capacity=None,
                    variable_costs=self.EPS,
                )},
            )
            es.add(battery_model)
            batteries["vehicle"][v_id] = {
                "model": battery_model,
                "bus": v_info["bus"],
                "soc": None,  # set after model simulation
                "capacity": vehicle.battery.capacity,
            }

        # setup charging station connections between GC and vehicles
        for cs_id, cs_v_info in cs_models.items():
            cs_charging = pd.Series(0, index=time_index)
            cs = self.world_state.charging_stations[cs_id]
            gc_bus = gcs[cs.parent]
            for v_id, v_info in cs_v_info.items():
                cs_charging += v_info["cs_charging"]
                v_bus = vehicle_models[v_id]["bus"]
                vehicle = self.world_state.vehicles[v_id]
                conn = solph.components.Link(
                    label=f"Conn_{cs_id}_{v_id}",
                    inputs={gc_bus: solph.Flow(variable_costs=self.EPS), v_bus: solph.Flow(
                        nominal_capacity=cs.max_power,
                        maximum=v_info["cs_charging"],  # allow charging when vehicle is present
                        minimum=0,
                        variable_costs=self.EPS,
                    )},
                    outputs={gc_bus: solph.Flow(variable_costs=self.EPS), v_bus: solph.Flow(
                        nominal_capacity=cs.max_power,
                        maximum=v_info["cs_charging"],  # allow discharging when vehicle is present
                        minimum=0,
                        variable_costs=self.EPS,
                    )},
                    conversion_factors={
                        # allow discharge for V2G only
                        (v_bus, gc_bus): int(vehicle.vehicle_type.v2g),
                        # lossless connection when charging
                        (gc_bus, v_bus): 1,
                    },
                )
                es.add(conn)
                cs_models[cs_id][v_id]["conn"] = conn

            # sanity check: only one charging vehicle allowed at a time
            if any(cs_charging > 1):
                raise Exception(f"CS {cs_id}: multiple charging at same time")

        # solve energy system model
        t2 = perf_counter()
        model = solph.Model(es)
        model.solve()
        # model.solve(solver="cbc", solve_kwargs={"tee": True})
        results = solph.Results(model)
        # save results for debugging
        self.results = results
        print("oemof solphed")
        t3 = perf_counter()

        # save computed SoC
        model_info = {  # oemof battery model -> battery info dict
            info["model"]: info
            for info in list(batteries["vehicle"].values()) + list(batteries["stationary"].values())
        }
        for model, values in results["storage_content"].items():
            battery_info = model_info[model]
            battery_info["soc"] = list(values / battery_info["capacity"])[1:]
        self.batteries = batteries

        # set current time index to access SoC list during step
        self.idx = 0

        print("init done")
        t4 = perf_counter()
        print("Setup system:", t2-t1)
        print("Solve system:", t3-t2)
        print("Extract cmds:", t4-t3)
        print("--- Total ---", t4-t1)

    def step(self):
        charging_stations = dict()
        # read out precomputed SoC, charge as needed
        for v_id, vehicle in self.world_state.vehicles.items():
            cs_id = vehicle.connected_charging_station
            if cs_id is None:
                # not connected: can't charge
                continue
            desired_soc = self.batteries["vehicle"][v_id]["soc"][self.idx]
            if abs(vehicle.battery.soc - desired_soc) < self.EPS:
                # precomputed SoC alread reached, no change needed
                continue
            cs = self.world_state.charging_stations[cs_id]
            gc = self.world_state.grid_connectors[cs.parent]
            if vehicle.battery.soc < desired_soc:
                p = vehicle.battery.load(self.interval, target_soc=desired_soc)["avg_power"]
            else:
                p = -vehicle.battery.unload(self.interval, target_soc=desired_soc)["avg_power"]
            charging_stations[cs_id] = gc.add_load(cs_id, p)

        for b_id, battery in self.world_state.batteries.items():
            desired_soc = self.batteries["stationary"][b_id]["soc"][self.idx]
            if abs(battery.soc - desired_soc) < self.EPS:
                # precomputed SoC alread reached, no change needed
                continue
            gc = self.world_state.grid_connectors[battery.parent]
            if battery.soc < desired_soc:
                p = battery.load(self.interval, target_soc=desired_soc)["avg_power"]
            else:
                p = -battery.unload(self.interval, target_soc=desired_soc)["avg_power"]
            gc.add_load(b_id, p)
        self.idx += 1

        return {'current_time': self.current_time, 'commands': charging_stations}


def get_battery_parameters(charging_curve) -> dict:
    """
    Get specific characteristics of a battery's charging curve:
    - power: charging power at lower SoC, should mostly be constant
    - until: when does the decrease in charging power begin, in terms of SoC (0.1)?
    - end_p: charging power when battery is full (never more than constant_power, None: no decrease)

    :param charging_curve: a battery's charging curve
    :type charging_curve: spice_ev.loading_curve.LoadingCurve
    :return: specific charging curve info
    :rtype: dict
    :raises Exception: if charging curve has more than one break or increases after break
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
