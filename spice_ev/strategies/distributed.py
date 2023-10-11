from copy import deepcopy
import datetime

from spice_ev import events, strategy
from spice_ev.components import Components


class Distributed(strategy.Strategy):
    """ Strategy that allows for greedy charging at opp stops and balanced charging at depots. """
    def __init__(self, components, start_time, **kwargs):
        super().__init__(components, start_time, **kwargs)
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
            components, start_time, **strat_options_opps)
        self.strat_deps = strategy.class_from_str(strat_deps)(
            components, start_time, **strat_options_deps)

        # minimum charging time at depot; time to look into the future for prioritization
        self.C_HORIZON = 3  # min
        # dict that holds the current vehicles connected to a grid connector for each gc
        self.v_connect = {gc_id: [] for gc_id in self.world_state.grid_connectors.keys()}

    def step(self):
        """ Calculates charging power in each timestep.

        :return: current time and commands of the charging stations
        :rtype: dict
        """

        # get power that can be drawn from battery in this timestep
        avail_bat_power = {gc_id: 0 for gc_id in self.world_state.grid_connectors}
        for bat in self.world_state.batteries.values():
            avail_bat_power[bat.parent] += bat.get_available_power(self.interval)

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
            if not skip_prioritization[gc_id]:
                vehicle_list = self.v_connect[gc_id]
            else:
                vehicle_list = []
                for v_id, v in self.world_state.vehicles.items():
                    cs_id = v.connected_charging_station
                    if cs_id and self.world_state.charging_stations[cs_id].parent == gc_id:
                        vehicle_list.append(v_id)

            # assumption: one GC each for every station/depot
            strat = None
            for v_id in vehicle_list:
                # get vehicle
                v = self.world_state.vehicles[v_id]
                # get connected charging station
                cs_id = v.connected_charging_station
                if cs_id is None:
                    continue
                cs = self.world_state.charging_stations[cs_id]
                if cs.parent != gc_id:
                    # vehicle may still be at prior stop
                    continue

                # get station type
                if strat is None:
                    station_type = cs_id.split("_")[-1]
                    if station_type == "opps":
                        strat = self.strat_opps
                    elif station_type == "deps":
                        strat = self.strat_deps
                    else:
                        print(f"The station {cs.parent} has no declaration such as "
                              "'opps' or 'deps' attached. Please make sure the "
                              "ending of the station name is one of the mentioned.")
                        continue

                    # create new empty world state
                    new_world_state = Components(dict())
                    # link to vehicle_types and photovoltaics (should not change during simulation)
                    new_world_state.vehicle_types = self.world_state.vehicle_types
                    new_world_state.photovoltaics = self.world_state.photovoltaics
                    # copy reference of current GC and relevant vehicles
                    # changes during simulation reflect back to original!
                    new_world_state.grid_connectors = {gc_id: gc}
                    # add available battery power
                    gc.cur_max_power += avail_bat_power[gc_id]
                    new_world_state.charging_stations = {}
                    new_world_state.vehicles = {}
                    # no batteries (do this here and not in substrategy)
                    new_world_state.batteries = {}
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

                new_world_state.charging_stations[cs_id] = cs
                new_world_state.vehicles[v_id] = v
                for event in self.world_state.future_events:
                    if type(event) is events.VehicleEvent and event.vehicle_id == v_id:
                        new_world_state.future_events.append(deepcopy(event))
            if strat is not None:
                # update world state of strategy
                strat.current_time = self.current_time
                strat.world_state = new_world_state
                commands = strat.step()["commands"]
                charging_stations.update(commands)
                # revert available battery power
                gc.cur_max_power -= avail_bat_power[gc_id]

        # all vehicles loaded
        charging_stations.update(self.distribute_surplus_power())
        # use bus specific strategy for charging stationary batteries
        # always charge battery if power is available on gc
        # since priority is to keep busses fully charged instead of
        # reducing peak load to a minimum
        for b_id, battery in self.world_state.batteries.items():
            gc = self.world_state.grid_connectors[battery.parent]
            gc_current_load = gc.get_current_load()
            if gc_current_load <= gc.cur_max_power:
                # GC suffices to meet busses needs
                power = gc.cur_max_power - gc_current_load
                power = 0 if power < battery.min_charging_power else power
                avg_power = battery.load(self.interval, max_power=power)['avg_power']
                gc.add_load(b_id, avg_power)
            else:
                # current load > max load, use battery to support GC
                # current load never rises above sum of max load of GC and available battery power
                power_needed = gc_current_load - gc.cur_max_power
                bat_power = battery.unload(self.interval, target_power=power_needed)
                gc.add_load(b_id, -bat_power['avg_power'])

        return {'current_time': self.current_time, 'commands': charging_stations}
