import oemof.solph as solph
import pandas as pd
from spice_ev.events import FixedLoad, GridOperatorSignal, LocalEnergyGeneration
from spice_ev.strategy import Strategy

from time import perf_counter

"""
TODO
min/max power

change to incentive: desired_soc, CS min power
"""

class OemofSolph(Strategy):
    """ Create and solve energy system with oemof"""
    def __init__(self, components, start_time, **kwargs):
        t1 = perf_counter()
        self.FORCE_DESIRED_SOC = True
        super().__init__(components, start_time, **kwargs)
        self.description = "oemof"

        time_index = pd.date_range(
            start=start_time,
            end=kwargs["stop_time"],
            freq=kwargs["interval"],
            inclusive="both",
        )
        n_intervals = kwargs["n_intervals"]
        assert len(time_index) - 1 == n_intervals, f"Length mismatch: index {len(time_index)} vs n_intervals {n_intervals}"

        def time_to_index(datetime):
            return int(-(start_time - datetime) / self.interval)

        es = solph.EnergySystem(
            timeindex=time_index,
            infer_last_interval=False,
        )

        # GC
        gcs = dict()
        gc_update = {gc_id: {
            "cost": pd.Series(gc.cost.get("value"), index=time_index[:-1]),
            "max_power": gc.max_power,
            "scalar": True,
            "fixed_load": dict(),
            "generation": dict(),
        } for gc_id, gc in self.world_state.grid_connectors.items()}
        for event in kwargs["events"].grid_operator_signals:
            if type(event) != GridOperatorSignal:
                # ignore schedule (type VehicleEvent)
                continue
            idx = time_to_index(event.start_time)
            gc_id = event.grid_connector_id
            if event.cost is not None:
                if event.cost["type"] != "fixed":
                    # special case: polynomial with only fixed part
                    if event.cost["type"] == "polynomial" and len(event.cost["value"]) > 1:
                        value = event.cost["value"][1]
                        if sum(event.cost["value"]) == value:
                            gc_update[gc_id]["cost"][idx:] = value
                            continue
                    raise Exception("Only fixed costs allowed")
                gc_update[gc_id]["cost"][idx:] = event.cost["value"]
            gc_power = gc_update[gc_id]["max_power"]
            if gc_power is not None:
                if event.max_power is not None:
                    if gc_update[gc_id]["scalar"] and gc_power != event.max_power:
                        # was scalar, but changed: change to list
                        gc_power = pd.Series(gc_power, index=time_index[:-1])
                        gc_power[idx:] = event.max_power
                        gc_update[gc_id]["max_power"] = gc_power
                        gc_update[gc_id]["scalar"] = False
                    else:
                        gc_power[idx:] = event.max_power
            else:
                # connector power not set
                gc_power = pd.Series(None, index=time_index[:-1])
                gc_power[idx:] = event.max_power
                gc_update[gc_id]["max_power"] = gc_power
                gc_update[gc_id]["scalar"] = False

        for name, load_list in kwargs["events"].fixed_load_lists.items():
            gc_id = load_list.grid_connector_id
            series = pd.Series(0.0, index=time_index[:-1])
            for event in load_list.get_events(name, FixedLoad):
                idx = time_to_index(event.start_time)
                series[idx:] = event.value
            gc_update[gc_id]["fixed_load"][name] = series

        for name, gen_list in kwargs["events"].local_generation_lists.items():
            gc_id = gen_list.grid_connector_id
            series = pd.Series(0.0, index=time_index[:-1])
            for event in gen_list.get_events(name, LocalEnergyGeneration):
                idx = time_to_index(event.start_time)
                series[idx:] = event.value
            gc_update[gc_id]["generation"][name] = series

        gcs = dict()
        for gc_id in self.world_state.grid_connectors.keys():
            gc_bus = solph.Bus(label=f"GC_{gc_id}")
            es.add(solph.components.Source(
                label=f"GC_{gc_id}_source",
                outputs={gc_bus: solph.Flow(
                    nominal_capacity=gc_update[gc_id]["max_power"],
                    variable_costs=gc_update[gc_id]["cost"],
                )}
            ))
            es.add(solph.components.Sink(
                label=f"GC_{gc_id}_sink",
                inputs={gc_bus: solph.Flow(
                    nominal_capacity=gc_update[gc_id]["max_power"],
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

        # stationary batteries
        batteries = dict()
        for bat_id, battery in self.world_state.batteries.items():
            battery_parameters = get_battery_parameters(battery.loading_curve)
            battery_model = solph.components.GenericStorage(
                label=f"Stationary_{bat_id}",
                nominal_capacity=battery.capacity,
                initial_storage_level=battery.soc,
                balanced=False,
                constant_soc_until=battery_parameters["until"],
                fraction_saturation_charging=battery_parameters["end_p"],
                inflow_conversion_factor=battery.efficiency,
                outflow_conversion_factor=battery.efficiency,
                inputs={gcs[battery.parent]: solph.Flow(
                    nominal_capacity=battery_parameters["power"],
                    variable_costs = self.EPS,  # avoid cycle charging
                )},
                outputs={gcs[battery.parent]: solph.Flow(
                    nominal_capacity=battery_parameters["power"],
                )},
            )
            es.add(battery_model)
            batteries[bat_id] = (gcs[battery.parent], battery_model)

        # get interpolation points for vehicle type charging curves
        charging_curve_info = dict()
        for vt in self.world_state.vehicle_types.values():
            charging_curve_info[vt] = get_battery_parameters(vt.charging_curve)

        # vehicle -> connected LIS -> time range
        vehicle_charging = {v_id: dict() for v_id in self.world_state.vehicles.keys()}
        connected_to = dict()
        spent = {v_id: 0 for v_id in self.world_state.vehicles.keys()}
        for event in kwargs["events"].vehicle_events:
            v_id = event.vehicle_id
            if event.event_type == "arrival":
                cs_id = event.update.get("connected_charging_station")
                spent[v_id] -= event.update.get("soc_delta", 0)
                if cs_id is None:
                    continue
                if cs_id not in vehicle_charging[v_id]:
                    vehicle_charging[v_id][cs_id] = {
                        "conn": None,
                        "events": list(),
                    }
                vehicle_charging[v_id][cs_id]["events"].append({
                    "start": event.start_time,
                    "end": event.update["estimated_time_of_departure"],
                    "spent": spent[v_id],
                    "desired": event.update["desired_soc"] if self.FORCE_DESIRED_SOC else 0,
                })
                connected_to[v_id] = cs_id
                spent[v_id] = 0
            if event.event_type == "departure":
                if v_id not in connected_to:
                    # first event is departure -> read info from vehicle
                    vehicle = self.world_state.vehicles[v_id]
                    if vehicle.connected_charging_station is None:
                        continue
                    vehicle_charging[v_id][vehicle.connected_charging_station] = {
                        "conn": None,
                        "events": [{
                            "start": start_time,
                            "end": event.start_time,
                            "spent": 0,
                            "desired": vehicle.desired_soc if self.FORCE_DESIRED_SOC else 0,
                        }],
                    }
                else:
                    cs_id = connected_to[v_id]
                    if cs_id is not None:
                        vehicle_charging[v_id][cs_id]["events"][-1]["end"] = event.start_time
                        connected_to[v_id] = None

        street = solph.components.Sink(label="driving", inputs=dict())
        es.add(street)

        for v_id, vehicle in self.world_state.vehicles.items():
            v_bus = solph.Bus(label=f"V_{v_id}")
            es.add(v_bus)

            driving = pd.Series(0.0, index=time_index[:-1])
            desired = pd.Series(0.0, index=time_index)
            last_departure = 0
            for cs_id, charge_events in vehicle_charging[v_id].items():
                charging = pd.Series(0, index=time_index[:-1])
                for event in charge_events["events"]:
                    start_idx = time_to_index(event["start"])
                    end_idx = time_to_index(event["end"])
                    charging.iloc[start_idx:end_idx] = 1
                    if end_idx < n_intervals:
                        desired.iloc[end_idx] = event["desired"]
                    if start_idx > 0:
                        # start_idx = 0: charging begins at scenario start -> no prior driving
                        steps = start_idx - last_departure
                        energy = event["spent"] * vehicle.battery.capacity * vehicle.battery.efficiency
                        if steps == 0 and energy > self.EPS:
                            raise Exception("no time for driving")
                        else:
                            avg_power = energy / steps * self.ts_per_hour
                            driving.iloc[last_departure:start_idx] =  avg_power
                    last_departure = end_idx
                cs = self.world_state.charging_stations[cs_id]
                gc_bus = gcs[cs.parent]

                conn = solph.components.Link(
                    label=f"Conn_{cs_id}_{v_id}",
                    inputs={gc_bus: solph.Flow(variable_costs = self.EPS), v_bus: solph.Flow(
                        nominal_capacity=cs.max_power,
                        maximum=charging,
                        minimum=0,
                        variable_costs=self.EPS,
                    )},
                    outputs={gc_bus: solph.Flow(variable_costs = self.EPS), v_bus: solph.Flow(
                        nominal_capacity=cs.max_power,
                        maximum=charging,
                        minimum=0,
                        variable_costs=self.EPS,
                    )},
                    conversion_factors={
                        # (v_bus, gc_bus): vehicle.vehicle_type.v2g_power_factor if vehicle.vehicle_type.v2g else 0,
                        (v_bus, gc_bus): int(vehicle.vehicle_type.v2g),
                        (gc_bus, v_bus): 1,
                    },
                )
                es.add(conn)
                charge_events["conn"] = (conn, v_bus)
            street.inputs[v_bus] = solph.Flow(
                nominal_capacity=1,
                fix=driving,
            )

            charging_curve = charging_curve_info[vehicle.vehicle_type]
            es.add(solph.components.GenericStorage(
                label=f"Bat_{v_id}",
                nominal_capacity=vehicle.battery.capacity,
                initial_storage_level=vehicle.battery.soc,
                balanced=False,
                min_storage_level=desired,
                constant_soc_until=charging_curve["until"],
                fraction_saturation_charging=charging_curve["end_p"],
                inflow_conversion_factor=vehicle.battery.efficiency,
                outflow_conversion_factor=vehicle.battery.efficiency,
                inputs={v_bus: solph.Flow(
                    nominal_capacity=charging_curve["power"],
                    variable_costs = self.EPS,  # avoid cycle charging
                )},
                outputs={v_bus: solph.Flow(
                    nominal_capacity=charging_curve["power"],
                    variable_costs = self.EPS,
                )},
            ))

        """
        import matplotlib.pyplot as plt
        import networkx as nx
        from oemof.network.graph import create_nx_graph
        plt.figure()
        # graph = es.to_nx_graph()
        graph = create_nx_graph(es)
        # nx.drawing.nx_pydot.write_dot(graph, "es.dot")
        nx.draw(graph, with_labels=True, font_size=8)
        plt.show()
        """

        # model = solph.Model(es)
        # model.solve()
        # results = solph.processing.results(model)
        # results = model.solve(solver="cbc", solve_kwargs={"tee": True})

        t2 = perf_counter()
        model = solph.Model(es)
        model.solve()
        # model.solve(solver="cbc", solve_kwargs={"tee": True})
        results = solph.Results(model)
        print("oemof solphed")
        t3=perf_counter()

        # print(results["flow"].to_string())

        """
        import matplotlib.pyplot as plt
        plt.figure()
        plt.subplot(len(gcs) + 1, 1, 1)
        plt.plot(results["storage_content"], label=results["storage_content"].columns)
        plt.legend()
        for i, gc_id in enumerate(gcs.keys()):
            plt.subplot(len(gcs) + 1, 1, i+2)
            plt.plot(results["flow"]["GC_" + gc_id], label=results["flow"]["GC_" + gc_id].columns)
            plt.legend()
            # k0 = [k for k in results["flow"].keys() if k[0] == "GC_" + gc_id]
            # k1 = [k for k in results["flow"].keys() if k[1] == "GC_" + gc_id]
            # plt.subplot(len(gcs) + 1, 1, i+2)
            # plt.plot(results["flow"][k0], label=results["flow"][k0].columns)
            # plt.plot(-results["flow"][k1], label=results["flow"][k1].columns)
            # plt.legend()
        # plt.plot(results["flow"], label=results["flow"].columns)
        # plt.legend()
        # plt.subplot(212)
        plt.show()
        breakpoint()
        """

        """
        # plot vehicles
        vehicles = dict()
        import matplotlib.pyplot as plt
        for v_id, vehicle_info in vehicle_charging.items():
            vehicle = self.world_state.vehicles[v_id]
            capacity = vehicle.battery.capacity
            soc = results["storage_content"][f"Bat_{v_id}"] / capacity
            for cs_id, charging in vehicle_info.items():
                conn = charging["conn"]
                flow_in = results["flow"][conn[0]][conn[1]]
                flow_out = results["flow"][conn[1]][conn[0]]
                flow = flow_in - flow_out
            vehicles[v_id] = {"soc": soc, "flow": flow}

        plt.figure()
        plt.subplot(211)
        for v_id, vehicle_info in vehicles.items():
            plt.plot(vehicle_info["soc"], label=v_id)
        plt.legend()
        plt.subplot(212)
        for v_id, vehicle_info in vehicles.items():
            plt.plot(vehicle_info["flow"], label=v_id)
        plt.legend()
        plt.show()
        """

        """
        # plot stationary batteries
        import matplotlib.pyplot as plt
        plt.figure()
        plt.subplot(211)
        for b_id, conn in batteries.items():
            battery = self.world_state.batteries[b_id]
            plt.plot(results["storage_content"][conn[1]] / battery.capacity, label=b_id)
        plt.legend()
        plt.subplot(212)
        for b_id, conn in batteries.items():
            plt.plot(results["flow"][conn[0]][conn[1]] - results["flow"][conn[1]][conn[0]], label=b_id)
        plt.legend()
        plt.show()
        """

        self.steps = [dict() for _ in range(n_intervals)]
        for v_id, charging_info in vehicle_charging.items():
            storage = list(results["storage_content"][f"Bat_{v_id}"])
            vehicle = self.world_state.vehicles[v_id]
            capacity = vehicle.battery.capacity
            for cs_id, charging in charging_info.items():
                conn = charging["conn"]
                flow_in = results["flow"][conn[0]][conn[1]]
                flow_out = results["flow"][conn[1]][conn[0]]
                flow = list(flow_in - flow_out)
                for idx, power in enumerate(flow):
                    if abs(power) < self.EPS:
                        # no flow at this timestep
                        continue
                    if cs_id in self.steps[idx]:
                        raise Exception(f"Double flow for {cs_id} at index {idx}")
                    if power < 0 and not vehicle.vehicle_type.v2g:
                        raise Exception(f"{v_id} is not V2G, but has negative flow")
                    self.steps[idx]["v_" + v_id] = (power, storage[idx + 1] / capacity)

        for b_id, conn in batteries.items():
            battery = self.world_state.batteries[b_id]
            storage = results["storage_content"][conn[1]]
            flow_in = results["flow"][conn[0]][conn[1]]
            flow_out = results["flow"][conn[1]][conn[0]]
            flow = list(flow_in - flow_out)
            for idx, power in enumerate(flow):
                if abs(power) > self.EPS:
                    self.steps[idx]["b_" + b_id] = (float(power), float(storage.iloc[idx+1]/battery.capacity))

        print("init done")
        t4 = perf_counter()
        print(f"Setup system: {t2-t1}\nSolve system: {t3-t2}\nExtract cmds: {t4-t3}\n--- Total --- {t4-t1}")


    def step(self):
        commands = self.steps.pop(0)
        charging_stations = dict()
        for id, (power, desired_soc) in commands.items():
            if id[0] == "v":
                v_id = id[2:]
                vehicle = self.world_state.vehicles[v_id]
                cs_id = vehicle.connected_charging_station
                if cs_id is None:
                    raise Exception(f"Vehicle {v_id} should be charging at {cs_id}, but is absent")
                if cs_id in charging_stations:
                    raise Exception(f"Double load at {cs_id}")
                cs = self.world_state.charging_stations[cs_id]
                gc = self.world_state.grid_connectors[cs.parent]
                if power > 0:
                    p = vehicle.battery.load(self.interval, target_soc=desired_soc)['avg_power']
                else:
                    p = -vehicle.battery.unload(self.interval, target_soc=desired_soc)['avg_power']
                charging_stations[cs_id] = gc.add_load(cs_id, p)
                battery = vehicle.battery
            elif id[0] == "b":
                b_id = id[2:]
                battery = self.world_state.batteries[b_id]
                gc = self.world_state.grid_connectors[battery.parent]
                if power > 0:
                    p = battery.load(self.interval, target_soc=desired_soc)['avg_power']
                else:
                    p = -battery.unload(self.interval, target_soc=desired_soc)['avg_power']
                gc.add_load(b_id, p)
            if abs(p - power) > self.EPS:
                print(f"{self.current_time} Power diverged: {power} != {p}. ID {id}. Continuing...")
            if abs(desired_soc - battery.soc) > self.EPS:
                print("Soc diverged", id, desired_soc, battery.soc)
                # breakpoint()
                raise Exception(f"SoC diverged: {desired_soc} != {battery.soc}. ID {id}")

        return {'current_time': self.current_time, 'commands': charging_stations}


def get_battery_parameters(charging_curve) -> dict:
    """
    Get specific characteristics of a battery's charging curve:
    - power: charging power at lower SoC, should mostly be constant
    - until: when does the decrease in charging power begin, in terms of SoC (0.1)?
    - end_p: charging power when battery is full (never more than constant_power, None: no decrease)
    return: dict
    """
    constant_power = None
    constant_soc_until = None
    end_power = None
    for soc, power in charging_curve.points:
        if soc == 0:
            constant_power = power
            constant_soc_until = 0
        elif soc < 1:
            if power == constant_power:
                constant_soc_until = soc
            else:
                raise Exception("Power must remain constant until break in charging curve")
        elif soc == 1:
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
