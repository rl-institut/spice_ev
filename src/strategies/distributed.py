import json

from src.util import clamp_power, get_cost
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
        self.description = "greedy"
        self.ITERATIONS = 12
        self.MIN_CHARGING_TIME = 2

    def step(self, event_list=[]):
        super().step(event_list)
        with open(self.ELECTRIFIED_STATIONS_FILE) as json_file:
            electrified_stations = json.load(json_file)

        # get power that can be drawn from battery in this timestep
        avail_bat_power = {}
        for gcID, gc in self.world_state.grid_connectors.items():
            avail_bat_power[gcID] = 0
            for bat in self.world_state.batteries.values():
                if bat.parent == gcID:
                    avail_bat_power[gcID] += bat.get_available_power(self.interval)

        # dict to hold charging commands
        charging_stations = {}
        # reset charging station power (nothing charged yet in this timestep)
        for cs in self.world_state.charging_stations.values():
            cs.current_power = 0

        current_and_future_events = sorted(self.world_state.future_events + event_list, key=lambda
                                           x: x.start_time)

        vehicle_index = 0
        # loop over all vehicles in this timestep
        for vehicle in self.world_state.vehicles.values():
            vehicle_id = list(self.world_state.vehicles)[vehicle_index]
            vehicle_index += 1
            cs_id = vehicle.connected_charging_station
            if cs_id is None:
                # not connected
                continue
            cs = self.world_state.charging_stations[cs_id]
            gc = self.world_state.grid_connectors[cs.parent]

            load = True
            # check if standing time is > MIN_CHARGING_TIME
            if (vehicle.estimated_time_of_departure -
               vehicle.estimated_time_of_arrival).total_seconds() / 60.0 < self.MIN_CHARGING_TIME:
                load = False
            # if number of cs is limited get other connected vehicles in this and future timesteps
            timesteps = []
            if gc.number_cs and load:
                # get all other vehicles connected to gc in this timestep
                for index, current_vehicle_id in enumerate(self.world_state.vehicles):
                    v = self.world_state.vehicles[current_vehicle_id]
                    if v.connected_charging_station is None or current_vehicle_id == vehicle_id:
                        continue
                    current_gc = self.world_state.charging_stations[
                                 v.connected_charging_station].parent
                    if current_gc == cs.parent:
                        timesteps.append({"vehicle_id": current_vehicle_id,
                                          "time_of_arrival": v.estimated_time_of_arrival,
                                          "time_of_departure": v.estimated_time_of_departure,
                                          "soc": v.battery.soc,
                                          "gc": current_gc})

                # look ahead (limited by departure_time)
                # get future arrival events and precalculate the soc of the incoming vehicles
                for event in current_and_future_events:
                    # peek into future events
                    if event.start_time > vehicle.estimated_time_of_departure:
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
                                current_gc = self.world_state.charging_stations[current_cs].parent
                                if (current_gc == cs.parent) and (
                                    event.update["estimated_time_of_departure"] - event.start_time
                                   ).total_seconds() / 60.0 >= self.MIN_CHARGING_TIME:
                                    current_soc_delta = event.update["soc_delta"]
                                    current_soc = self.world_state.vehicles[current_vehicle_id]\
                                                      .battery.soc - current_soc_delta

                                    # save infos for each timestep
                                    timesteps.append({"vehicle_id":  current_vehicle_id,
                                                      "time_of_arrival": event.start_time,
                                                      "time_of_departure": event.update
                                                      ["estimated_time_of_departure"],
                                                      "soc": current_soc,
                                                      "gc": current_gc})

            # do not load if other busses connected at gc have lower soc that current vehicle.
            if timesteps:
                if gc.number_cs:
                    if len(timesteps) > gc.number_cs - 1:
                        # if current vehicle does not have lowest soc set load to false
                        if not all(i >= vehicle.battery.soc for i in [t["soc"] for t in timesteps]):
                            load = False

            if load:
                if cs.parent in electrified_stations["opp_stations"]:
                    charging_stations = greedy.load_vehicle_greedy(self, cs, gc, vehicle, cs_id, charging_stations, avail_bat_power)
                    # load batteries
                    greedy.load_batteries_greedy(self)
                elif cs.parent in electrified_stations["depot_stations"]:
                    charging_stations = balanced.load_vehicle_balanced(self, cs, gc, vehicle, cs_id, charging_stations, avail_bat_power)
                    # load batteries
                    greedy.load_batteries_greedy(self)
#                    charging_stations = self.load_balanced(cs, gc, vehicle,
#                                                           cs_id, charging_stations)
                else:
                    print(f"The station {cs.parent} is not in {self.electrifies_stations_file}. "
                          f"Please check for consistency.")

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

                if cs.parent in electrified_stations["opp_stations"]:
                    charging_stations = greedy.add_surplus_to_vehicle(self, cs, gc, vehicle, cs_id,
                                                                      charging_stations)
                elif cs.parent in electrified_stations["depot_stations"]:
                    charging_stations = balanced.add_surplus_to_vehicle(self, cs, gc, vehicle, cs_id,
                                                                        charging_stations)
                else:
                    print(f"The station {cs.parent} is not in {self.electrifies_stations_file}. "
                          f"Please check for consistency.")

            # charge/discharge batteries
            balanced.load_batteries(self)


        return {'current_time': self.current_time, 'commands': charging_stations}


