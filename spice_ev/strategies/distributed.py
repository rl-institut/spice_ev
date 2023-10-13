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

        # minimum charging time at depot; time to look into the future for prioritization
        self.C_HORIZON = 3  # min
        # dict that holds the current vehicles connected to a grid connector for each gc
        self.v_connect = {gc_id: [] for gc_id in self.world_state.grid_connectors.keys()}

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
        for b_id, battery in self.world_state.batteries.items():
            # make note to run GC even if no vehicles are connected
            if self.gc_battery.get(battery.parent):
                self.gc_battery[battery.parent][b_id] = battery
            else:
                self.gc_battery[battery.parent] = {b_id: battery}

            station_type = self.strategies.get(battery.parent)
            if station_type is None or station_type[0] == "deps":
                # only batteries at opportunity stations need preparation
                continue
            name = f"stationary_{b_id}"
            # create new V2G vehicle type
            self.virtual_vt[name] = components.VehicleType({
                "name": name,
                "capacity": battery.capacity,
                "charging_curve": battery.charging_curve.points,
                "min_charging_power": battery.min_charging_power,
                "battery_efficiency": battery.efficiency,
                "v2g": True,
                "discharge_curve": battery.discharge_curve,
            })
            # set up virtual charging station
            self.virtual_cs[name] = components.ChargingStation({
                "parent": battery.parent,
                "max_power": battery.charging_curve.max_power,
                "min_power": battery.min_charging_power,
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

        skip_prioritization = {}
        # rank which vehicles should be loaded at gc
        for gc_id, gc in self.world_state.grid_connectors.items():
            if gc.number_cs is None:
                skip_prioritization[gc_id] = True
                continue
            else:
                skip_prioritization[gc_id] = False
            # update v_connect: only vehicles that are connected and below desired SoC remain
            still_connected = []
            for v_id in self.v_connect[gc_id]:
                v = self.world_state.vehicles[v_id]
                if v.connected_charging_station is not None:
                    cs = self.world_state.charging_stations[v.connected_charging_station]
                    if cs.parent == gc_id and v.get_delta_soc() > self.EPS:
                        still_connected.append(v_id)
            self.v_connect[gc_id] = still_connected
            # number of charging vehicles must not exceed maximum allowed for this GC
            assert len(self.v_connect[gc_id]) <= self.world_state.grid_connectors[gc_id].number_cs

            # check if available loading stations are already taken
            if len(self.v_connect[gc_id]) == gc.number_cs:
                continue

            timesteps = []
            # filter vehicles that are connected to gc in this time step
            for vehicle_id, v in self.world_state.vehicles.items():
                cs_id = v.connected_charging_station
                if cs_id and self.world_state.charging_stations[cs_id].parent == gc_id:
                    timesteps.append({"vehicle_id": vehicle_id,
                                      "time_of_arrival": v.estimated_time_of_arrival,
                                      "time_of_departure": v.estimated_time_of_departure,
                                      "soc": v.battery.soc,
                                      "gc": gc_id})
            # look ahead (limited by C-HORIZON)
            # get additional future arrival events and precalculate the soc of the incoming vehicles
            for event in self.world_state.future_events:
                # peek into future events
                if event.start_time > event.start_time + datetime.timedelta(minutes=self.C_HORIZON):
                    # not this timestep
                    break
                if type(event) is events.VehicleEvent:
                    if event.vehicle_id == vehicle_id:
                        # not this vehicle event
                        continue
                    else:
                        current_vehicle_id = event.vehicle_id
                        if (event.event_type == "arrival") and \
                                event.update["connected_charging_station"] is not None:
                            event_cs = event.update["connected_charging_station"]
                            event_gc = self.world_state.charging_stations[
                                event_cs].parent
                            if event_gc == gc_id:
                                current_soc_delta = event.update["soc_delta"]
                                current_soc = self.world_state.vehicles[current_vehicle_id] \
                                                  .battery.soc - current_soc_delta

                                # save infos for each timestep
                                timesteps.append({"vehicle_id": current_vehicle_id,
                                                  "time_of_arrival": event.start_time,
                                                  "time_of_departure": event.update
                                                  ["estimated_time_of_departure"],
                                                  "soc": current_soc,
                                                  "gc": gc_id})

            # get number of free spots
            free_spots = gc.number_cs - len(self.v_connect[gc_id])
            while free_spots > 0 and timesteps:
                # get vehicle with lowest soc
                v_id_min = min(timesteps, key=lambda x: x['soc'])["vehicle_id"]
                # add vehicle to v-connect, if it is not already in list
                if v_id_min not in self.v_connect[gc_id]:
                    self.v_connect[gc_id].append(v_id_min)
                    free_spots = gc.number_cs - len(self.v_connect[gc_id])
                timesteps = [i for i in timesteps if not (i['vehicle_id'] == v_id_min)]

        # all vehicles are ranked. Load vehicles that are in v_connect
        for gc_id, gc in self.world_state.grid_connectors.items():
            # find all vehicles that are actually connected
            vehicles = self.world_state.vehicles if skip_prioritization else self.v_connect[gc_id]
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
                        bat_vehicle = components.Vehicle({
                            "vehicle_type": name,
                            "connected_charging_station": name,
                            "soc": battery.soc,
                            "estimated_time_of_departure": str(self.current_time + self.interval),
                        }, self.virtual_vt)
                        new_world_state.vehicle_types[name] = self.virtual_vt[name]
                        new_world_state.charging_stations[name] = self.virtual_cs[name]
                        new_world_state.vehicles[b_id] = bat_vehicle
                        # bat_vehicle.desired_soc = int(not bool(connected_vehicles))
                        if connected_vehicles:
                            # busses present at station: discharge
                            bat_vehicle.desired_soc = 0
                        else:
                            # no busses present: charge greedy
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
