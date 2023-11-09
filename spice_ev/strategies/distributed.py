from copy import deepcopy
import datetime

from spice_ev import components, events, strategy, util


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
                self.strategies[cs.parent] = (station_type, deepcopy(self.strat_deps))
            elif station_type == "opps":
                self.strategies[cs.parent] = (station_type, deepcopy(self.strat_opps))
            else:
                raise Exception(f"The station {cs.parent} has no declaration such as "
                                "'opps' or 'deps' attached. Please make sure the "
                                "ending of the station name is one of the mentioned.")

        # prepare separate world states of grid connectors
        # only shallow copy, so that changes are reflected
        for gc_id, (station_type, strat) in self.strategies.items():
            # single GC (some strategies only work with one GC)
            # also, only one GC is relevant for each station
            strat.world_state.grid_connectors = {gc_id: self.world_state.grid_connectors[gc_id]}
            # filter charging stations
            strat.world_state.charging_stations = {
                cs_id: cs for cs_id, cs in self.world_state.charging_stations.items()
                if cs.parent == gc_id}
            # vehicle types, vehicles and photovoltaics (shallow) copied
            strat.world_state.vehicle_types = self.world_state.vehicle_types.copy()
            strat.world_state.vehicles = self.world_state.vehicles.copy()
            strat.world_state.photovoltaics = self.world_state.photovoltaics.copy()
            # batteries: only retain at depot (opportunity stations: simulate as vehicle)
            strat.world_state.batteries = {
                b_id: battery for b_id, battery in self.world_state.batteries.items()
                if battery.parent == gc_id and station_type == "deps"}

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
            # create new vehicle type equivalent to battery (only charging, no discharging)
            strat = self.strategies[bat.parent][1]
            strat.world_state.vehicle_types[name] = components.VehicleType({
                "name": name,
                "capacity": bat.capacity,
                "charging_curve": bat.charging_curve.points,
                "min_charging_power": bat.min_charging_power,
                "battery_efficiency": bat.efficiency,
            })
            # set up virtual charging station
            strat.world_state.charging_stations[name] = components.ChargingStation({
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
            if event.start_time <= self.current_time + self.CHARGE_HORIZON:
                # arrival within charging horizon
                vehicle = self.world_state.vehicles.get(event.vehicle_id)
                if vehicle is None:
                    continue
                soc = vehicle.battery.soc - event.update["soc_delta"]
                if soc < event.update["desired_soc"]:
                    arriving[event_cs.parent].append({
                        "vehicle_id": event.vehicle_id,
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

            # set time window if needed
            station_type, strat = self.strategies[gc_id]
            try:
                gc.window = util.datetime_within_time_window(
                    self.current_time, strat.time_windows.get(gc.grid_operator), gc.voltage_level)
            except Exception:
                gc.window = None

            if connected_vehicles or self.gc_battery.get(gc_id):
                # GC needs to be simulated

                # filter future events for this GC
                strat.world_state.future_events = []
                for event in self.world_state.future_events:
                    if (
                            type(event) in [
                                events.FixedLoad,
                                events.LocalEnergyGeneration,
                                events.GridOperatorSignal]
                            and event.grid_connector_id == gc_id):
                        strat.world_state.future_events.append(deepcopy(event))

                # stationary batteries
                avail_bat_power = dict()
                if station_type == "opps":
                    # opportunity station:
                    # - charge according to strategy (simulate by creating equivalent vehicle)
                    # - discharge when needed power is above GC max power
                    for b_id, battery in self.gc_battery.get(gc_id, {}).items():
                        if connected_vehicles:
                            # vehicle present: support GC (increase GC max power)
                            power = battery.get_available_power(self.interval)
                            if power < battery.min_charging_power:
                                # below minimum (dis)charging power
                                continue
                            # get difference in SoC (too small changes are ignored
                            # and would lead to difference in available battery power)
                            total_time = self.interval.total_seconds() / 3600
                            energy_delta = power / battery.efficiency * total_time
                            soc_delta = energy_delta / battery.capacity
                            if soc_delta < self.EPS:
                                # remaining power too small
                                continue
                            avail_bat_power[b_id] = (power, gc.cur_max_power)
                            gc.cur_max_power += power
                        else:
                            # vacant station: charge with strategy until vehicle arrives
                            name = f"stationary_{b_id}"
                            arrive = next_arrival.get(gc_id, self.current_time+self.ARRIVAL_HORIZON)
                            bat_vehicle = components.Vehicle({
                                "vehicle_type": name,
                                "connected_charging_station": name,
                                "soc": battery.soc,
                                "desired_soc": 1,
                                "estimated_time_of_departure": str(arrive),
                            }, strat.world_state.vehicle_types)
                            strat.world_state.vehicles[b_id] = bat_vehicle

                # update world state of strategy
                strat.current_time = self.current_time
                # run sub-strategy
                commands = strat.step()["commands"]
                # update stationary batteries
                if station_type == "opps":
                    for b_id, battery in self.gc_battery.get(gc_id, {}).items():
                        power = avail_bat_power.get(b_id)
                        if power is not None:
                            # battery used to support GC -> revert max_power, discharge
                            gc.cur_max_power = power[1]
                            power_needed = gc.get_current_load() - gc.cur_max_power
                            power = battery.unload(self.interval, target_power=max(power_needed, 0))
                            gc.add_load(b_id, -power['avg_power'])
                            continue
                        name = f"stationary_{b_id}"
                        if name in commands:
                            # battery is simulated as vehicle -> apply changes
                            # remove from commands
                            del commands[name]
                            # and add as battery
                            # this will crash if virtual CS power has not been added correctly to GC
                            gc.add_load(b_id, gc.current_loads.pop(name))
                            # update battery SoC
                            battery.soc = strat.world_state.vehicles[b_id].battery.soc
                        # remove simulated battery vehicle from world state
                        strat.world_state.vehicles.pop(b_id, None)
                charging_stations.update(commands)

        # all vehicles charged
        charging_stations.update(self.distribute_surplus_power())

        return {'current_time': self.current_time, 'commands': charging_stations}
