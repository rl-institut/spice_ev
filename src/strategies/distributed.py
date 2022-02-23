import datetime

from src.strategy import Strategy
from src import events
from src.strategies import greedy
from src.strategies import balanced


class Distributed(Strategy):
    """
    Strategy that allows for greedy charging at opp stops and balanced charging at depots.
    """
    def __init__(self, constants, start_time, **kwargs):
        self.PRICE_THRESHOLD = 0.001  # EUR/kWh
        super().__init__(constants, start_time, **kwargs)
        self.description = "distributed"
        self.ITERATIONS = 12
        self.MIN_CHARGING_TIME = 2
        self.C_HORIZON = 3  # min
        self.V_CONNECT = {}

    def step(self, event_list=[]):
        super().step(event_list)

        # get power that can be drawn from battery in this timestep
        avail_bat_power = {}
        for gcID, gc in self.world_state.grid_connectors.items():
            avail_bat_power[gcID] = 0
            for bat in self.world_state.batteries.values():
                if bat.parent == gcID:
                    avail_bat_power[gcID] += bat.get_available_power(self.interval)

        # dict to hold charging commands
        charging_stations = {}
        # reset charging station power (nothing charged yet in this time step)
        for cs in self.world_state.charging_stations.values():
            cs.current_power = 0

        current_and_future_events = sorted(self.world_state.future_events + event_list, key=lambda
                                           x: x.start_time)
        skip_priorization = {}
        # rank which cars should be loaded at gc
        for gcID, gc in self.world_state.grid_connectors.items():
            if gc.number_cs is None:
                skip_priorization[gcID] = True
                continue
            else:
                skip_priorization[gcID] = False
            if gcID not in self.V_CONNECT.keys():
                self.V_CONNECT[gcID] = []
            # remove vehicle from c-line if it left already or their desired_soc is reached
            if self.V_CONNECT[gcID]:
                for v_id in self.V_CONNECT[gcID]:
                    v = self.world_state.vehicles[v_id]
                    if v.connected_charging_station is None or v.battery.soc > 0.8:
                        self.V_CONNECT[gcID].remove(v_id)
            # check if length connected vehicles is smaller or same than cs_number
            assert len(self.V_CONNECT[gcID]) <= self.world_state.grid_connectors[gcID].number_cs

            # check if available loading stations are already taken
            if len(self.V_CONNECT[gcID]) == gc.number_cs:
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
            for event in current_and_future_events:
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
                            current_cs = event.update["connected_charging_station"]
                            current_gc = self.world_state.charging_stations[
                                current_cs].parent
                            if current_gc == cs.parent:
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

            # add number of freee spots' vehicles with lowest soc to V_CONNECT
            free_spots = gc.number_cs - len(self.V_CONNECT[gcID])
            while free_spots > 0 and timesteps:
                # get vehicle with lowest soc
                v_id_min = min(timesteps, key=lambda x: x['soc'])["vehicle_id"]
                # add vehicle to v-connect, if it is not already in list
                if v_id_min not in self.V_CONNECT[gcID]:
                    self.V_CONNECT[gcID].append(v_id_min)
                    free_spots = gc.number_cs - len(self.V_CONNECT[gcID])
                timesteps = [i for i in timesteps if not (i['vehicle_id'] == v_id_min)]

        # all cars are ranked. Now load cars that are in V_CONNECT if prioritized
        for gcID, gc in self.world_state.grid_connectors.items():
            if not skip_priorization[gcID]:
                vehicle_list = self.V_CONNECT[gcID]
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
                if not cs_id:
                    continue
                cs = self.world_state.charging_stations[cs_id]
                # get station type
                station_type = cs_id.split("_")[-1]
                if station_type == "opp":
                    charging_stations = greedy.load_vehicle(self, cs, gc, v, cs_id,
                                                            charging_stations,
                                                            avail_bat_power[gcID])
                    # load batteries
                    greedy.load_batteries(self)
                elif station_type == "depot":
                    charging_stations = balanced.load_vehicle(self, cs, gc, v, cs_id,
                                                              charging_stations,
                                                              avail_bat_power[gcID])
                    # load batteries
                    greedy.load_batteries(self)
                else:
                    print(f"The station {cs.parent} has no declaration such as 'opp' or 'depot'"
                          f"attached. Please make sure the ending of the station name is one of the"
                          f"mentioned.")

            # all vehicles loaded
            # distribute surplus power to vehicles
            # power is clamped to CS max_power (with concurrency, see init)
            for vehicle_id in sorted(self.world_state.vehicles):
                vehicle = self.world_state.vehicles[vehicle_id]
                cs_id = vehicle.connected_charging_station
                if cs_id is None:
                    continue
                cs = self.world_state.charging_stations[cs_id]
                gc = self.world_state.grid_connectors[cs.parent]
                station_type = cs_id.split("_")[-1]
                if station_type == "opp":
                    charging_stations = greedy.add_surplus_to_vehicle(self, cs, gc, vehicle, cs_id,
                                                                      charging_stations)
                elif station_type == "depot":
                    charging_stations = balanced.add_surplus_to_vehicle(self, cs, gc, vehicle,
                                                                        cs_id, charging_stations)
                else:
                    print(f"The station {cs.parent} has no declaration such as 'opp' or 'depot'"
                          f"attached. Please make sure the ending of the station name is one of the"
                          f"mentioned.")

            # charge/discharge batteries
            balanced.load_batteries(self)

        return {'current_time': self.current_time, 'commands': charging_stations}