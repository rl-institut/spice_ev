import datetime

from spice_ev import events
from spice_ev.strategy import Strategy
from spice_ev.strategies import greedy, balanced


class Distributed(Strategy):
    """
    Strategy that allows for greedy charging at opp stops and balanced charging at depots.
    """
    def __init__(self, components, start_time, **kwargs):
        self.PRICE_THRESHOLD = 0.001  # EUR/kWh
        super().__init__(components, start_time, **kwargs)
        self.description = "distributed"
        self.ITERATIONS = 12
        # minimum charging time at depot; time to look into the future for prioritization
        self.C_HORIZON = 3  # min
        # dict that holds the current vehicles connected to a grid connector for each gc
        self.v_connect = {gcID: [] for gcID in self.world_state.grid_connectors.keys()}

    def step(self):

        # get power that can be drawn from battery in this timestep
        avail_bat_power = {gcID: 0 for gcID in self.world_state.grid_connectors}
        for bat in self.world_state.batteries.values():
            avail_bat_power[bat.parent] += bat.get_available_power(self.interval)

        # dict to hold charging commands
        charging_stations = {}
        # reset charging station power (nothing charged yet in this time step)
        for cs in self.world_state.charging_stations.values():
            cs.current_power = 0

        skip_priorization = {}
        # rank which vehicles should be loaded at gc
        for gcID, gc in self.world_state.grid_connectors.items():
            if gc.number_cs is None:
                skip_priorization[gcID] = True
                continue
            else:
                skip_priorization[gcID] = False
            # update v_connect: only vehicles that are connected and below desired SoC remain
            still_connected = []
            for v_id in self.v_connect[gcID]:
                v = self.world_state.vehicles[v_id]
                if v.connected_charging_station is not None:
                    cs = self.world_state.charging_stations[v.connected_charging_station]
                    if cs.parent == gcID and v.get_delta_soc() > self.EPS:
                        still_connected.append(v_id)
            self.v_connect[gcID] = still_connected
            # number of charging vehicles must not exceed maximum allowed for this GC
            assert len(self.v_connect[gcID]) <= self.world_state.grid_connectors[gcID].number_cs

            # check if available loading stations are already taken
            if len(self.v_connect[gcID]) == gc.number_cs:
                continue

            timesteps = []
            # filter vehicles that are connected to gc in this time step
            for vehicle_id, v in self.world_state.vehicles.items():
                cs_id = v.connected_charging_station
                if cs_id and self.world_state.charging_stations[cs_id].parent == gcID:
                    timesteps.append({"vehicle_id": vehicle_id,
                                      "time_of_arrival": v.estimated_time_of_arrival,
                                      "time_of_departure": v.estimated_time_of_departure,
                                      "soc": v.battery.soc,
                                      "gc": gcID})
            # look ahead (limited by C-HORIZON)
            # get additional future arrival events and precalculate the soc of the incoming vehicles
            for event in self.world_state.future_events:
                # peek into future events
                if event.start_time > event.start_time + datetime.timedelta(minutes=self.C_HORIZON):
                    # not this timestep
                    break
                if type(event) == events.VehicleEvent:
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
                            if event_gc == gcID:
                                current_soc_delta = event.update["soc_delta"]
                                current_soc = self.world_state.vehicles[current_vehicle_id] \
                                                  .battery.soc - current_soc_delta

                                # save infos for each timestep
                                timesteps.append({"vehicle_id": current_vehicle_id,
                                                  "time_of_arrival": event.start_time,
                                                  "time_of_departure": event.update
                                                  ["estimated_time_of_departure"],
                                                  "soc": current_soc,
                                                  "gc": gcID})

            # get number of free spots
            free_spots = gc.number_cs - len(self.v_connect[gcID])
            while free_spots > 0 and timesteps:
                # get vehicle with lowest soc
                v_id_min = min(timesteps, key=lambda x: x['soc'])["vehicle_id"]
                # add vehicle to v-connect, if it is not already in list
                if v_id_min not in self.v_connect[gcID]:
                    self.v_connect[gcID].append(v_id_min)
                    free_spots = gc.number_cs - len(self.v_connect[gcID])
                timesteps = [i for i in timesteps if not (i['vehicle_id'] == v_id_min)]

        # all vehicles are ranked. Load vehicles that are in v_connect
        for gcID, gc in self.world_state.grid_connectors.items():
            if not skip_priorization[gcID]:
                vehicle_list = self.v_connect[gcID]
            else:
                vehicle_list = []
                for v_id, v in self.world_state.vehicles.items():
                    cs_id = v.connected_charging_station
                    if cs_id and self.world_state.charging_stations[cs_id].parent == gcID:
                        vehicle_list.append(v_id)
            for v_id in vehicle_list:
                # get vehicle
                v = self.world_state.vehicles[v_id]
                # get connected charging station
                cs_id = v.connected_charging_station
                if cs_id is None:
                    continue
                cs = self.world_state.charging_stations[cs_id]
                if cs.parent != gcID:
                    # vehicle may still be at prior stop
                    continue
                # get station type
                station_type = cs_id.split("_")[-1]
                if station_type == "opps":
                    charging_stations, avail_bat_power[gcID] = \
                        greedy.load_vehicle(self, cs, gc, v, cs_id, charging_stations,
                                            avail_bat_power[gcID])
                elif station_type == "deps":
                    charging_stations, avail_bat_power[gcID] = \
                        balanced.load_vehicle(self, cs, gc, v, cs_id, charging_stations,
                                              avail_bat_power[gcID])
                else:
                    print(f"The station {cs.parent} has no declaration such as 'opps' or 'deps'"
                          f"attached. Please make sure the ending of the station name is one of the"
                          f"mentioned.")

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
                avg_power = battery.load(self.interval, power)['avg_power']
                gc.add_load(b_id, avg_power)
            else:
                # current load > max load, use battery to support GC
                # current load never rises above sum of max load of GC and available battery power
                power_needed = gc_current_load - gc.cur_max_power
                bat_power = battery.unload(self.interval, target_power=power_needed)
                gc.add_load(b_id, -bat_power['avg_power'])

        return {'current_time': self.current_time, 'commands': charging_stations}
