import json

from src.util import clamp_power, get_cost
from src.strategy import Strategy
from src import events



class Distributed(Strategy):
    """
    Strategy that allows for greedy charging at opp stops and balanced charging at depots.
    """
    def __init__(self, constants, start_time, **kwargs):
        self.electrifies_stations_file = "./examples/electrified_stations1.json" #todo: add this to config file
        self.PRICE_THRESHOLD = 0.001  # EUR/kWh
        super().__init__(constants, start_time, **kwargs)
        self.description = "greedy"
        self.ITERATIONS = 12
        self.MIN_CHARGING_TIME = 2

    def step(self, event_list=[]):
        super().step(event_list)
        with open(self.electrifies_stations_file) as json_file:
            electrified_stations = json.load(json_file)

        # dict to hold charging commands
        charging_stations = {}
        # reset charging station power (nothing charged yet in this timestep)
        for cs in self.world_state.charging_stations.values(): #todo: do we need this?
            cs.current_power = 0

        current_and_future_events = sorted(self.world_state.future_events + event_list, key=lambda  x: x.start_time)

        # sort for soc and serve vehicle with lowest soc first
        vehicle_index = 0
        # sorted(self.world_state.vehicles.values(), key=lambda x: x.battery.soc)
        for vehicle in self.world_state.vehicles.values():
            vehicle_id = list(self.world_state.vehicles)[vehicle_index]
            vehicle_index += 1
            cs_id = vehicle.connected_charging_station
            if cs_id is None:
                # not connected
                continue
            cs = self.world_state.charging_stations[cs_id]
            gc = self.world_state.grid_connectors[cs.parent]

            # get all other vehicles connected to gc in this timestep
            timesteps = []
            for index, current_vehicle_id in enumerate(self.world_state.vehicles):
                v = self.world_state.vehicles[current_vehicle_id]
                if v.connected_charging_station is None or current_vehicle_id == vehicle_id:
                    continue
                current_gc = self.world_state.charging_stations[v.connected_charging_station].parent
                if current_gc == cs.parent:
                    timesteps.append({"vehicle_id": current_vehicle_id,
                                      "time_of_arrival": v.estimated_time_of_arrival,
                                      "time_of_departure": v.estimated_time_of_departure,
                                      "soc": v.battery.soc,
                                      "gc": current_gc})

            # ---------- GET FUTURE VEHICLE EVENTS ---------- #
            # look ahead (limited by departure_time)
            # get future arrival events and precalculate the soc of the incoming vehicles
            cur_time = self.current_time # - self.interval
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
                            if (current_gc == cs.parent) and (event.update["estimated_time_of_departure"] -
                                event.start_time).total_seconds() / 60.0 > self.MIN_CHARGING_TIME:
                                current_soc_delta = event.update["soc_delta"]
                                current_soc = self.world_state.vehicles[current_vehicle_id]\
                                                  .battery.soc - current_soc_delta

                                #save infos for each timestep
                                timesteps.append({"vehicle_id":  current_vehicle_id,
                                                  "time_of_arrival": event.start_time,
                                                  "time_of_departure": event.update
                                                  ["estimated_time_of_departure"],
                                                  "soc": current_soc,
                                                  "gc": current_gc})
            # ----------------------------------------------- #

            # do not load if other busses connected at gc have lower soc that current vehicle.
            load = True
            if timesteps:
                if gc.number_cs:
                    if len(timesteps) > gc.number_cs - 1:
                        # if current vehicle does not have lowest soc set load to false
                        if not all(i >= vehicle.battery.soc for i in [t["soc"] for t in timesteps]):
                            load = False

            if load:
                if cs.parent in electrified_stations["opp_stations"]:
                    charging_stations = self.load_greedy(cs, gc, vehicle, cs_id, charging_stations)
                elif cs.parent in electrified_stations["depot_stations"]:
                    charging_stations = self.load_balanced(cs, gc, vehicle, cs_id, charging_stations)
                else:
                    print(f"The station {cs.parent} is not in {self.electrifies_stations_file}. Please "
                          f"check for consistency.")

        return {'current_time': self.current_time, 'commands': charging_stations}

    def load_greedy(self, cs, gc, vehicle, cs_id, charging_stations):

        gc_power_left = gc.cur_max_power - gc.get_current_load()
        power = 0
        avg_power = 0
        bat_power_used = False
        if get_cost(1, gc.cost) <= self.PRICE_THRESHOLD:
            # low energy price: take max available power from GC without batteries
            power = clamp_power(gc_power_left, vehicle, cs)
            avg_power = vehicle.battery.load(self.interval, power)['avg_power']
        elif vehicle.get_delta_soc() > 0:
            # vehicle needs charging: take max available power (with batteries)
            # limit to desired SoC
            power = gc_power_left
            power = clamp_power(power, vehicle, cs)
            avg_power = vehicle.battery.load(
                self.interval, power, target_soc=vehicle.desired_soc)['avg_power']

        # update CS and GC
        charging_stations[cs_id] = gc.add_load(cs_id, avg_power)
        cs.current_power += avg_power

        return charging_stations


    def load_balanced(self, cs, gc, vehicle, cs_id, charging_stations):

        gc_power_left = gc.cur_max_power - gc.get_current_load()
        power = 0
        delta_soc = vehicle.get_delta_soc()

        if get_cost(1, gc.cost) <= self.PRICE_THRESHOLD:
            # low energy price: take max available power from GC without batteries
            power = clamp_power(gc_power_left, vehicle, cs)
        elif delta_soc > self.EPS:
            # vehicle needs charging: compute minimum required power
            # get limits
            min_power = max(vehicle.vehicle_type.min_charging_power, cs.min_power)
            max_power = gc_power_left
            max_power = min(max_power, vehicle.vehicle_type.charging_curve.max_power)
            max_power = clamp_power(max_power, vehicle, cs)
            # time until departure
            dt = vehicle.estimated_time_of_departure - self.current_time
            old_soc = vehicle.battery.soc
            idx = 0
            safe = False
            # converge to optimal power for the duration
            # at least ITERATIONS cycles
            # must end with slightly too much power used
            # abort if min_power == max_power (e.g. unrealistic goal)
            while (idx < self.ITERATIONS or not safe) and max_power - min_power > self.EPS:
                idx += 1
                # get new power value (binary search: use average)
                power = (max_power + min_power) / 2
                # load whole time with same power
                charged_soc = vehicle.battery.load(dt, power)["soc_delta"]
                # reset SOC
                vehicle.battery.soc = old_soc

                if delta_soc - charged_soc > self.EPS:  # charged_soc < delta_soc
                    # power not enough
                    safe = False
                    min_power = power
                else:  # charged_soc >= delta_soc:
                    # power too much or just right (may be possible with less power)
                    safe = True
                    max_power = power

        # load with power
        avg_power = vehicle.battery.load(self.interval, power)['avg_power']
        charging_stations[cs_id] = gc.add_load(cs_id, avg_power)
        cs.current_power += avg_power

        # can active charging station bear minimum load?
        assert cs.max_power >= cs.current_power - self.EPS, (
            "{} - {} over maximum load ({} > {})".format(
                self.current_time, cs_id, cs.current_power, cs.max_power))
        # can grid connector bear load?
        assert gc.cur_max_power >= gc.get_current_load() - self.EPS, (
            "{} - {} over maximum load ({} > {})".format(
                self.current_time, cs.parent, gc.get_current_load(), gc.cur_max_power))

        return charging_stations