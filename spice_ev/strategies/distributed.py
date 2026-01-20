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
        self.description = f"distributed (deps: {strat_deps} / opps: {strat_opps})"
        self.strat_opps = strategy.class_from_str(strat_opps)(
            comps, start_time, **strat_options_opps)
        self.strat_deps = strategy.class_from_str(strat_deps)(
            comps, start_time, **strat_options_deps)

        # adjust foresight for vehicle events (known one hour in advance)
        self.ARRIVAL_HORIZON = datetime.timedelta(hours=1)
        # minimum charging time at depot; time to look into the future for prioritization
        self.CHARGE_HORIZON = datetime.timedelta(minutes=3)
        for event in self.events.vehicle_events:
            event.signal_time = min(event.signal_time, event.start_time-self.ARRIVAL_HORIZON)

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
            # create look-up-table for GC ID -> battery dict
            if self.gc_battery.get(bat.parent):
                self.gc_battery[bat.parent][b_id] = bat
            else:
                self.gc_battery[bat.parent] = {b_id: bat}

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
            if event.start_time <= self.current_time + self.CHARGE_HORIZON:
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

        # rank which vehicles should be charged at gc
        skip_prio = {}
        for gc_id, gc in gcs.items():
            if gc.number_cs is None:
                skip_prio[gc_id] = True
                continue
            else:
                skip_prio[gc_id] = False
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

        # all vehicles are ranked. Charge vehicles that are connected
        for gc_id, gc in self.world_state.grid_connectors.items():
            # find all vehicles that are actually connected
            vehicles = self.world_state.vehicles if skip_prio[gc_id] else self.connected[gc_id]
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
                strat.gc_power[gc_id] = gc.cur_max_power

                # filter future events for this GC (within event horizon)
                new_world_state.future_events = []
                try:
                    strat_horizon = datetime.timedelta(hours=strat.HORIZON)
                except AttributeError:
                    # not all strategies have an event horizon: use no foresight
                    strat_horizon = datetime.timedelta(0)
                for event in self.world_state.future_events:
                    if event.start_time > self.current_time + strat_horizon:
                        break
                    if (
                            type(event) in [
                                events.FixedLoad,
                                events.LocalEnergyGeneration,
                                events.GridOperatorSignal]
                            and event.grid_connector_id == gc_id):
                        new_world_state.future_events.append(event)

                # only vehicle events for currently charging vehicles within horizon relevant
                for v_id, vehicle in connected_vehicles.items():
                    cs_id = vehicle.connected_charging_station
                    cs = self.world_state.charging_stations[cs_id]
                    new_world_state.charging_stations[cs_id] = cs
                    new_world_state.vehicles[v_id] = vehicle
                    for event in self.world_state.future_events:
                        if event.start_time > self.current_time + strat_horizon:
                            break
                        if type(event) is events.VehicleEvent and event.vehicle_id == v_id:
                            new_world_state.future_events.append(event)

                # stationary batteries
                gc_batteries = self.gc_battery.get(gc_id, dict())
                new_world_state.batteries = gc_batteries
                if strat.battery_strategy is not None:
                    # special stationary battery strategy: ignore charging strategy
                    # remove batteries from GC, so they can't charge
                    # add available battery power to GC power
                    for bat in gc_batteries.values():
                        available_power = bat.get_available_power(self.interval)
                        gc.cur_max_power += available_power
                        bat.parent = None

                # update world state of strategy
                strat.current_time = self.current_time
                strat.world_state = new_world_state
                # run sub-strategy
                commands = strat.step()["commands"]
                charging_stations.update(commands)
                # update batteries according to battery strategy
                strat.post_step()

        # all vehicles charged
        charging_stations.update(self.distribute_surplus_power())

        return {'current_time': self.current_time, 'commands': charging_stations}
