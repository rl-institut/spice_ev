from copy import deepcopy
import datetime

from spice_ev import components, events, strategy


class Distributed(strategy.Strategy):
    """ Strategy that allows for greedy charging at opp stops and balanced charging at depots. """
    def __init__(self, comps, start_time, **kwargs):
        super().__init__(comps, start_time, **kwargs)
        # create distinct strategies for depot and stations that get updated each step
        strat_opps = kwargs.get("strategy_opps", "greedy")
        # get substrategy options that override general options
        strat_options_opps = kwargs.pop("strategy_options_opps", dict())
        strat_options_deps = kwargs.pop("strategy_options_deps", dict())
        # dict.update does not return a value, use dict creation to retain values
        strat_options_opps = dict(deepcopy(kwargs), **strat_options_opps)
        strat_deps = kwargs.get("strategy_deps", "balanced")
        strat_options_deps = dict(deepcopy(kwargs), **strat_options_deps)
        self.description = f"distributed {strat_opps}/{strat_deps}"
        self.strat_opps = strategy.class_from_str(strat_opps)(
            comps, start_time, **strat_options_opps)
        self.strat_deps = strategy.class_from_str(strat_deps)(
            comps, start_time, **strat_options_deps)

        # adjust foresight for vehicle events (known one hour in advance)
        self.A_HORIZON = datetime.timedelta(hours=1)
        # minimum charging time at depot; time to look into the future for prioritization
        self.C_HORIZON = datetime.timedelta(minutes=3)
        for event in self.events.vehicle_events:
            event.signal_time = min(event.signal_time, event.start_time-self.A_HORIZON)

        # keep track of connected vehicles per GC (more vehicles might have arrived than CS)
        self.connected = {gc_id: dict() for gc_id in self.world_state.grid_connectors.keys()}
        # assumption: one GC each for every station/depot
        self.virtual_vt = dict()  # virtual vehicle types for stationary batteries
        self.virtual_cs = dict()  # virtual charging stations for stationary batteries
        self.strategies = dict()  # tuple of (station type, strategy) for each GC
        self.gc_battery = dict()  # GC ID -> batteries
        # set strategy for each GC
        for cs_id, cs in self.world_state.charging_stations.items():
            station_type = cs_id.split("_")[-1]
            prev_type = self.strategies.get(cs.parent)
            if prev_type is not None:
                assert prev_type[0] == station_type, f"Station types do not match at {cs.parent}"
                continue
            if station_type == "deps":
                self.strategies[cs.parent] = (station_type, self.strat_deps)
            elif station_type == "opps":
                self.strategies[cs.parent] = (station_type, self.strat_opps)
            else:
                raise Exception(f"The station {cs.parent} has no declaration such as "
                                "'opps' or 'deps' attached. Please make sure the "
                                "ending of the station name is one of the mentioned.")

        # prepare batteries
        for b_id, bat in self.world_state.batteries.items():
            # make note to run GC even if no vehicles are connected
            if self.gc_battery.get(bat.parent):
                self.gc_battery[bat.parent][b_id] = bat
            else:
                self.gc_battery[bat.parent] = {b_id: bat}

            station_type = self.strategies.get(bat.parent)
            if station_type is None or station_type[0] == "deps":
                # only batteries at opportunity stations need preparation
                continue
            name = f"stationary_{b_id}"
            # create new V2G vehicle type
            self.virtual_vt[name] = components.VehicleType({
                "name": name,
                "capacity": bat.capacity,
                "charging_curve": bat.charging_curve.points,
                "min_charging_power": bat.min_charging_power,
                "battery_efficiency": bat.efficiency,
                "v2g": True,
                "discharge_curve": bat.discharge_curve.points if bat.discharge_curve else None,
                "discharge_limit": 0,
            })
            # set up virtual charging station
            self.virtual_cs[name] = components.ChargingStation({
                "parent": bat.parent,
                "max_power": bat.charging_curve.max_power,
                "min_power": bat.min_charging_power,
            })

    def step(self):
        """ Calculates charging power in each timestep.

        :return: current time and commands of the charging stations
        :rtype: dict
        """

        # dict to hold charging commands
        charging_stations = {}
        # reset charging station power (nothing charged yet in this time step)
        for cs in self.world_state.charging_stations.values():
            cs.current_power = 0

        gcs = self.world_state.grid_connectors
        # look into future
        # take note of currently arrived vehicles, those arriving soon and
        # (if no vehicles are present) when new vehicles will arive
        arriving = {gc_id: [] for gc_id in gcs.keys()}
        next_arrival = dict()  # dt of next arrival
        for v_id, vehicle in self.world_state.vehicles.items():
            cs_id = vehicle.connected_charging_station
            cs = self.world_state.charging_stations.get(cs_id)
            if cs is not None and vehicle.get_delta_soc() > self.EPS:
                # at charging station and needs charging
                arriving[cs.parent].append({
                    "vehicle_id": v_id,
                    "time_of_departure": vehicle.estimated_time_of_departure,
                    "soc": vehicle.battery.soc,
                    "arrived": True,
                })
                next_arrival[cs.parent] = self.current_time

        for event in self.world_state.future_events:
            if type(event) is not events.VehicleEvent:
                continue
            if event.event_type != "arrival":
                # only interested in arrival events
                continue
            event_cs_id = event.update.get("connected_charging_station")
            event_cs = self.world_state.charging_stations.get(event_cs_id)
            if event_cs is None:
                continue
            if event.start_time <= self.current_time + self.C_HORIZON:
                # arrival within charging horizon
                v_id = event.vehicle_id
                soc = self.world_state.vehicles[v_id].battery.soc - event.update["soc_delta"]
                if soc < event.update["desired_soc"]:
                    arriving[event_cs.parent].append({
                        "vehicle_id": v_id,
                        "time_of_departure": event.update["estimated_time_of_departure"],
                        "soc": soc,
                        "arrived": False,
                    })
            if next_arrival.get(event_cs.parent) is None:
                # no prior arrival
                next_arrival[event_cs.parent] = event.start_time

        # rank which vehicles should be loaded at gc
        skip_prioritization = {}
        for gc_id, gc in gcs.items():
            if gc.number_cs is None:
                skip_prioritization[gc_id] = True
                continue
            else:
                skip_prioritization[gc_id] = False
            # filter out vehicles from connected that have left
            conn = {
                v_id: v for v_id, v in self.connected[gc_id].items()
                if v.connected_charging_station is not None}
            assert len(conn) <= gc.number_cs

            if len(conn) == gc.number_cs:
                # all CS occupied: no future arrivals
                self.connected[gc_id] = conn
                continue

            # add unconnected vehicles until all free spots are taken by order of soc
            arr_gc = [v for v in arriving[gc_id] if not v["vehicle_id"] in conn]
            arr_gc = sorted(arr_gc, key=lambda v: v["soc"])
            free_spots = gc.number_cs - len(conn)
            while free_spots > 0 and arr_gc:
                v_id = arr_gc.pop(0)["vehicle_id"]
                conn[v_id] = self.world_state.vehicles[v_id]
                free_spots -= 1
            assert len(conn) <= gc.number_cs
            self.connected[gc_id] = conn

        # all vehicles are ranked. Load vehicles that are connected
        for gc_id, gc in self.world_state.grid_connectors.items():
            # find all vehicles that are actually connected
            vehicles = self.world_state.vehicles if skip_prioritization else self.connected[gc_id]
            connected_vehicles = dict()
            for v_id, vehicle in vehicles.items():
                cs_id = vehicle.connected_charging_station
                if cs_id and self.world_state.charging_stations[cs_id].parent == gc_id:
                    connected_vehicles[v_id] = vehicle

            if connected_vehicles or self.gc_battery.get(gc_id):
                # GC needs to be simulated
                station_type, strat = self.strategies[gc_id]
                # prepare new empty world state
                new_world_state = components.Components(dict())
                # link to vehicle_types and photovoltaics (should not change during simulation)
                new_world_state.vehicle_types = self.world_state.vehicle_types
                new_world_state.photovoltaics = self.world_state.photovoltaics
                # copy reference of current GC and relevant vehicles
                # changes during simulation reflect back to original!
                new_world_state.grid_connectors = {gc_id: gc}

                # filter future events for this GC
                new_world_state.future_events = []
                for event in self.world_state.future_events:
                    if (
                            type(event) in [
                                events.FixedLoad,
                                events.LocalEnergyGeneration,
                                events.GridOperatorSignal]
                            and event.grid_connector_id == gc_id):
                        new_world_state.future_events.append(deepcopy(event))

                for v_id, vehicle in connected_vehicles.items():
                    cs_id = vehicle.connected_charging_station
                    cs = self.world_state.charging_stations[cs_id]
                    new_world_state.charging_stations[cs_id] = cs
                    new_world_state.vehicles[v_id] = vehicle
                    for event in self.world_state.future_events:
                        if type(event) is events.VehicleEvent and event.vehicle_id == v_id:
                            new_world_state.future_events.append(deepcopy(event))

                # stationary batteries
                if station_type == "deps":
                    # depot: use stationary batteries according to selected strategy
                    new_world_state.batteries = self.gc_battery.get(gc_id, {})
                else:
                    for b_id, battery in self.gc_battery.get(gc_id, {}).items():
                        name = f"stationary_{b_id}"
                        departure = next_arrival.get(gc_id, self.current_time + self.A_HORIZON)
                        bat_vehicle = components.Vehicle({
                            "vehicle_type": name,
                            "connected_charging_station": name,
                            "soc": battery.soc,
                            "estimated_time_of_departure": str(departure),
                        }, self.virtual_vt)
                        new_world_state.vehicle_types[name] = self.virtual_vt[name]
                        new_world_state.charging_stations[name] = self.virtual_cs[name]
                        new_world_state.vehicles[b_id] = bat_vehicle

                        if connected_vehicles:
                            # busses present at station: discharge
                            bat_vehicle.desired_soc = 0
                        else:
                            # no busses present: charge until bus arrives
                            bat_vehicle.desired_soc = 1

                # update world state of strategy
                strat.current_time = self.current_time
                strat.world_state = new_world_state
                # run sub-strategy
                commands = strat.step()["commands"]
                # update stationary batteries
                for b_id, battery in self.world_state.batteries.items():
                    name = f"stationary_{b_id}"
                    power = commands.pop(name, None)
                    if power is not None:
                        gc.add_load(name, -power)
                        gc.add_load(b_id, power)
                        battery.soc = strat.world_state.vehicles[b_id].battery.soc
                charging_stations.update(commands)

        # all vehicles loaded
        charging_stations.update(self.distribute_surplus_power())

        return {'current_time': self.current_time, 'commands': charging_stations}
