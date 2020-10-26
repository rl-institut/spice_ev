from copy import deepcopy
import datetime
import multiprocessing as mp
import random

import traceback
def tb(e):
    traceback.print_exception(type(e), e, e.__traceback__)

import events
import util

def class_from_str(strategy_name):
    strategy_name = strategy_name.lower()
    if   strategy_name == 'greedy':
        return Greedy
    elif strategy_name == 'parity':
        return Parity
    elif strategy_name == 'balanced':
        return Balanced
    elif strategy_name == 'foresight':
        return Foresight
    elif strategy_name == 'genetic':
        return Genetic
    else:
        raise Exception('unknown strategy with name {}'.format(strategy_name))


class Strategy():
    """ strategy
    """

    def __init__(self, constants, start_time, interval):
        self.world_state = deepcopy(constants)
        self.world_state.future_events = []
        self.current_time = start_time - interval
        self.interval = interval

    def step(self, event_list=[]):
        self.current_time += self.interval

        self.world_state.future_events += event_list
        self.world_state.future_events.sort(key = lambda ev: ev.start_time)

        while True:
            if len(self.world_state.future_events) == 0:
                break
            elif self.world_state.future_events[0].start_time > self.current_time:
                # ignore future events
                break

            # remove event from list
            ev = self.world_state.future_events.pop(0)

            if type(ev) == events.ExternalLoad:
                connector = self.world_state.grid_connectors[ev.grid_connector_id]
                assert ev.name not in self.world_state.charging_stations, "External load must not be from charging station"
                connector.current_loads[ev.name] = ev.value # not reset after last event
            elif type(ev) == events.GridOperatorSignal:
                connector = self.world_state.grid_connectors[ev.grid_connector_id]
                if ev.cost:
                    # set power cost
                    connector.cost = ev.cost
                # set max power from event
                if connector.max_power:
                    if ev.max_power:
                        connector.cur_max_power = min(connector.max_power, ev.max_power)
                    else:
                        # event max power not set: reset to connector power
                        connector.cur_max_power = connector.max_power
                else:
                    # connector max power not set
                    connector.cur_max_power = ev.max_power

            elif type(ev) == events.VehicleEvent:
                vehicle = self.world_state.vehicles[ev.vehicle_id]
                for k,v in ev.update.items():
                    setattr(vehicle, k, v)
                if ev.event_type == "departure":
                    vehicle.connected_charging_station = None
                    assert vehicle.battery.soc >= vehicle.desired_soc * 0.95, "{}: Vehicle {} is below desired SOC ({} < {})".format(ev.start_time.isoformat(), ev.vehicle_id, vehicle.battery.soc, vehicle.desired_soc)
                elif ev.event_type == "arrival":
                    assert vehicle.connected_charging_station is not None
                    assert hasattr(vehicle, 'soc_delta')
                    vehicle.battery.soc += vehicle.soc_delta
                    assert vehicle.battery.soc >= 0, 'SOC of vehicle {} should not be negative. SOC is {}, soc_delta was {}'.format(ev.vehicle_id, vehicle.battery.soc, vehicle.soc_delta)
                    delattr(vehicle, 'soc_delta')


            else:
                raise Exception("Unknown event type: {}".format(ev))

        for name, connector in self.world_state.grid_connectors.items():
            # reset charging stations at grid connector
            for load_name in list(connector.current_loads.keys()):
                if load_name in self.world_state.charging_stations.keys():
                    # connector.current_loads[load_name] = 0
                    del connector.current_loads[load_name]

            # check for associated costs
            if not connector.cost:
                raise Exception("Warning: Connector {} has no associated costs at {}".format(name, time))


class Greedy(Strategy):
    def __init__(self, constants, start_time, interval):
        super().__init__(constants, start_time, interval)
        self.description = "greedy"


    def step(self, event_list=[]):
        super().step(event_list)

        charging_stations = {}
        socs = {}

        for vehicle_id in sorted(self.world_state.vehicles):
            vehicle = self.world_state.vehicles[vehicle_id]
            delta_soc = vehicle.desired_soc - vehicle.battery.soc
            cs_id = vehicle.connected_charging_station
            if delta_soc > 0 and cs_id:
                cs = self.world_state.charging_stations[cs_id]
                # vehicle needs loading
                gc = self.world_state.grid_connectors[cs.parent]
                gc_power_left = gc.cur_max_power - sum(gc.current_loads.values())
                cs_power_left = cs.max_power - charging_stations.get(cs_id, 0)
                max_power =  min(cs_power_left, gc_power_left)

                load_result = vehicle.battery.load(self.interval, max_power)
                avg_power = load_result['avg_power']

                charging_stations[cs_id] = gc.add_load(cs_id, avg_power)

                assert vehicle.battery.soc <= 100
                assert vehicle.battery.soc >= 0, 'SOC of {} is {}'.format(vehicle_id, vehicle.battery.soc)

            socs[vehicle_id] = vehicle.battery.soc

        return {'current_time': self.current_time, 'commands': charging_stations, 'socs': socs}

class Parity(Strategy):
    """
    Charging strategy that distributes power evenly among cars.
    """
    def __init__(self, constants, start_time, interval):
        super().__init__(constants, start_time, interval)
        self.description = "parity"


    def step(self, event_list=[]):
        super().step(event_list)

        # charging vehicle at which grid connector?
        vehicle_to_grid = {}
        charging_stations = {}

        # gather all vehicles in need of charge
        for vehicle_id, vehicle in self.world_state.vehicles.items():
            delta_soc = vehicle.desired_soc - vehicle.battery.soc
            cs_id = vehicle.connected_charging_station
            if delta_soc > 0 and cs_id:
                cs = self.world_state.charging_stations[cs_id]
                gc_id = cs.parent
                if gc_id in vehicle_to_grid:
                    vehicle_to_grid[gc_id].append(vehicle_id)
                else:
                    vehicle_to_grid[gc_id] = [vehicle_id]

        # distribute power of each grid connector
        for gc_id, gc in self.world_state.grid_connectors.items():
            gc_power_left = gc.cur_max_power - sum(gc.current_loads.values())
            vehicles = vehicle_to_grid.get(gc_id, [])

            for vehicle_id in vehicles:
                vehicle = self.world_state.vehicles[vehicle_id]
                cs_id = vehicle.connected_charging_station
                cs = self.world_state.charging_stations[cs_id]
                vehicles_at_cs = list(filter(lambda v: self.world_state.vehicles[v].connected_charging_station == cs_id, vehicles))

                # find minimum of distributed power and charging station power
                gc_dist_power = gc_power_left / len(vehicles)
                gc_dist_power = min(gc_dist_power, cs.max_power)
                # CS guaranteed to have one requesting vehicle
                cs_dist_power = gc_dist_power / len(vehicles_at_cs)

                # load battery
                load_result = vehicle.battery.load(self.interval, cs_dist_power)
                avg_power = load_result['avg_power']

                charging_stations[cs_id] = gc.add_load(cs_id, avg_power)

                assert vehicle.battery.soc <= 100
                assert vehicle.battery.soc >= 0, 'SOC of {} is {}'.format(vehicle_id, vehicle.battery.soc)

        socs={vid: v.battery.soc for vid, v in self.world_state.vehicles.items()}

        return {'current_time': self.current_time, 'commands': charging_stations, 'socs': socs}


class Balanced(Strategy):
    """
    Charging strategy that calculates the minimum charging power to arrive at the
    desired SOC during the estimated parking time for each vehicle.
    """
    def __init__(self, constants, start_time, interval):
        super().__init__(constants, start_time, interval)
        self.description = "balanced"


    def step(self, event_list=[]):
        super().step(event_list)

        charging_stations = {}
        EPS = 1e-5
        ITERATIONS = 10

        for vehicle_id in sorted(self.world_state.vehicles):
            # get vehicle
            vehicle = self.world_state.vehicles[vehicle_id]
            delta_soc = vehicle.desired_soc - vehicle.battery.soc
            cs_id = vehicle.connected_charging_station
            if cs_id is None:
                # not connected
                continue
            # get connected charging station
            cs = self.world_state.charging_stations[cs_id]

            if delta_soc > EPS:
                # vehicle needs charging
                if cs.current_power == 0:
                    # not precomputed
                    min_power = vehicle.vehicle_type.min_charging_power
                    max_power = vehicle.vehicle_type.charging_curve.max_power
                    # time until departure
                    dt = vehicle.estimated_time_of_departure - self.current_time - datetime.timedelta(hours=1)
                    old_soc = vehicle.battery.soc
                    idx = 0
                    safe = False
                    # converge to optimal power for the duration
                    # at least ITERATIONS cycles
                    # must end with slightly too much power used
                    # abort if min_power == max_power (e.g. unrealistic goal)
                    while (idx < ITERATIONS or not safe) and max_power - min_power > EPS:
                        idx += 1
                        # get new power value
                        power = (max_power + min_power) / 2
                        # load whole time with same power
                        charged_soc = vehicle.battery.load(dt, power)["soc_delta"]
                        # reset SOC
                        vehicle.battery.soc = old_soc

                        if delta_soc - charged_soc > EPS: #charged_soc < delta_soc
                            # power not enough
                            safe = False
                            min_power = power
                        elif charged_soc - delta_soc > EPS: #charged_soc > delta_soc:
                            # power too much
                            safe = True
                            max_power = power
                        else:
                            # power exactly right
                            break

                    # add safety margin
                    # power *= 1.1
                    cs.current_power = power
                else:
                    # power precomputed: use again
                    power = cs.current_power

                gc = self.world_state.grid_connectors[cs.parent]
                gc_power_left = max(0, gc.cur_max_power - sum(gc.current_loads.values()))
                old_soc = vehicle.battery.soc
                # load with power
                avg_power = vehicle.battery.load(self.interval, power)['avg_power']
                if avg_power > gc_power_left:
                    # GC at limit: try again with less power
                    vehicle.battery.soc = old_soc
                    avg_power = vehicle.battery.load(self.interval, gc_power_left)['avg_power']
                    # compute new plan next time
                    cs.current_power = 0

                assert vehicle.battery.soc <= 100
                assert vehicle.battery.soc >= 0, 'SOC of {} is {}'.format(vehicle_id, vehicle.battery.soc)

                charging_stations[cs_id] = gc.add_load(cs_id, avg_power)

        # update charging stations
        for cs_id, cs in self.world_state.charging_stations.items():
            if cs_id not in charging_stations:
                # CS currently inactive
                cs.current_power = 0
            else:
                # can active charging station bear minimum load?
                assert cs.max_power >= cs.current_power - EPS, "{} - {} over maximum load ({} > {})".format(self.current_time, cs_id, cs.current_power, cs.max_power)
                # can grid connector bear load?
                gc = self.world_state.grid_connectors[cs.parent]
                gc_current_power = sum(gc.current_loads.values())
                assert  gc.cur_max_power >= gc_current_power - EPS, "{} - {} over maximum load ({} > {})".format(self.current_time, cs.parent, gc_current_power, gc.cur_max_power)

        socs={vid: v.battery.soc for vid, v in self.world_state.vehicles.items()}

        return {'current_time': self.current_time, 'commands': charging_stations, 'socs': socs}

class Foresight(Strategy):
    """
    Charging strategy that takes future available power and costs into account.
    """
    def __init__(self, constants, start_time, interval):
        super().__init__(constants, start_time, interval)
        self.description = "foresight"

        # prepare dictionary of predicted external load
        self.pred_ext_load = {}

        timesteps_per_day = int(datetime.timedelta(days=1) / interval)
        cur_time = start_time
        for _ in range(timesteps_per_day):
            for gc in self.world_state.grid_connectors.keys():
                self.pred_ext_load[str(cur_time.time())] = {
                    gc: 0 for gc in self.world_state.grid_connectors.keys()
                }
            cur_time += interval

    def step(self, event_list=[]):
        super().step(event_list)

        # update predicted external load
        timestamp = str(self.current_time.time())
        predicted_loads = self.pred_ext_load[timestamp]
        gc_info = {}
        for gc_id, gc in self.world_state.grid_connectors.items():
            predicted_load = predicted_loads[gc_id]
            actual_load = sum(gc.current_loads.values())
            predicted_loads[gc_id] = 0.25 * predicted_load + 0.75 * actual_load
            gc_info[gc_id] = (gc.cur_max_power, gc.cost)
        self.pred_ext_load[timestamp] = predicted_loads

        # reset charging station power
        for cs in self.world_state.charging_stations.values():
            cs.current_power = 0

        # gather current state of vehicles
        vehicles = {
            v_id: {
                "delta_energy": (v.desired_soc - v.battery.soc) / 100 * v.battery.capacity,
                "timesteps": 0
            } for v_id, v in self.world_state.vehicles.items()
        }

        # gather charging vehicles, external load and prices until all vehicles gone (24h max)
        future    = {}
        event_idx = 0
        timesteps_per_day = int(datetime.timedelta(days =1) / self.interval)
        timesteps_per_hour=     datetime.timedelta(hours=1) / self.interval

        cur_time = self.current_time - self.interval
        for _ in range(timesteps_per_day):
            cur_time += self.interval
            dts = str(cur_time)

            # get charging vehicles
            cur_vehicles = {gc: [] for gc in self.world_state.grid_connectors.keys()}
            for v_id, vehicle in self.world_state.vehicles.items():
                needs_charging = vehicle.battery.soc < vehicle.desired_soc
                still_present  = vehicle.estimated_time_of_departure > cur_time and vehicle.connected_charging_station is not None
                if still_present and needs_charging:
                    cs = self.world_state.charging_stations[vehicle.connected_charging_station]
                    cur_vehicles[cs.parent].append(v_id)
                    vehicles[v_id]["timesteps"] += 1

            if sum([len(a) for a in cur_vehicles.values()]):
                future[dts] = {gc: {
                    "vehicles": v
                } for gc, v in cur_vehicles.items()}
            else:
                # no vehicles to charge
                break

            # peek into future events for external load or cost changes
            # for event in self.world_state.future_events:
            while True:
                try:
                    event = self.world_state.future_events[event_idx]
                except IndexError:
                    break
                if event.start_time > cur_time:
                    break
                event_idx += 1
                if type(event) == events.ExternalLoad:
                    # update predicted external load
                    # update all future external loads as well?
                    # gc_id = event.grid_connector_id
                    # self.pred_ext_load[str(cur_time.time())][gc_id] = event.value
                    # TODO: find out if external load is only updated or new one
                    pass
                elif type(event) == events.GridOperatorSignal:
                    gc_id = event.grid_connector_id
                    max_power = event.max_power or gc_info[gc_id][0]
                    gc_info[gc_id][0] = min(gc_info[gc_id][0], max_power)
                    gc_info[gc_id][1] = event.cost
                elif type(event) == events.VehicleEvent:
                    # ignored: use current estimated arrival/departure times
                    pass

            # predicted external load
            cur_ext_load = self.pred_ext_load[str(cur_time.time())]

            # compute available power and associated costs
            for gc_id, gc in self.world_state.grid_connectors.items():
                available_power = gc_info[gc_id][0] - cur_ext_load[gc_id]
                # cost = util.get_cost(gc_info[gc_id][0], gc_info[gc_id][1])
                cost = util.get_cost(gc_info[gc_id][0], gc_info[gc_id][1])
                future[dts][gc_id]["power"] = available_power
                future[dts][gc_id]["costs"] = cost

        if len(future) == 0:
            # no charging
            socs={vid: v.battery.soc for vid, v in self.world_state.vehicles.items()}
            return {'current_time': self.current_time, 'commands': {}, 'socs': socs}

        charging_stations = {}
        current_state = future[str(self.current_time)]
        # assign charging power by grid connector
        for gc_id, gc in self.world_state.grid_connectors.items():
            vehicles_present = current_state[gc_id]["vehicles"]
            available_power = current_state[gc_id]["power"]
            costs = [f[gc_id]["costs"] for f in future.values()]

            # sort charging vehicles by remaining time
            vehicles_present = sorted(vehicles_present, key=lambda v_id: vehicles[v_id]["timesteps"])

            for v_id in vehicles_present:
                vehicle = self.world_state.vehicles[v_id]
                delta_energy = vehicles[v_id]["delta_energy"]
                timesteps = vehicles[v_id]["timesteps"]
                cs_id = vehicle.connected_charging_station
                cs = self.world_state.charging_stations[cs_id]
                mean_power = (delta_energy / timesteps) * timesteps_per_hour

                # get normed costs in remaining timesteps
                norm_costs = [c for c in costs[:timesteps]]
                min_costs  = min(norm_costs)
                max_costs  = max(norm_costs)
                for i in range(len(norm_costs)):
                    if min_costs == max_costs:
                        norm_costs[i] = 1
                    else:
                        norm_costs[i] = (norm_costs[i] - min_costs) / (max_costs - min_costs)
                sum_costs = sum(norm_costs)
                avg_costs = sum_costs / len(norm_costs)
                delta_costs = avg_costs - norm_costs[0]
                factor = 1 - delta_costs

                power = mean_power * factor
                power = min(available_power, power)
                power = min(cs.max_power - cs.current_power, power)
                avg_power = vehicle.battery.load(self.interval, power)['avg_power']
                available_power -= avg_power
                charging_stations[cs_id] = gc.add_load(cs_id, avg_power)
                cs.current_power += avg_power

        socs={vid: v.battery.soc for vid, v in self.world_state.vehicles.items()}
        return {'current_time': self.current_time, 'commands': charging_stations, 'socs': socs}

def fp(individual):
    return str(individual)

def fitness_function(idx, individual, vehicle_types, intervals, pred_gc, start_time, interval):

    # print(start_time, idx, vehicle_types, intervals, pred_gc, interval)
    current_time = start_time - interval
    assert individual[1] == None
    fitness = 0
    for ts_idx, ts_info in enumerate(individual[0]):
        num_ts = intervals[ts_idx]
        for _ in range(num_ts):
            current_time += interval
            gc_info = pred_gc[str(current_time.time())]
            power = gc_info['ext_load']
            power_left = gc_info['max_power'] - power
            for type_idx, vehicles in enumerate(vehicle_types):
                value = ts_info[type_idx]
                for vehicle in vehicles.values():
                    ind_power = value * power_left / len(vehicles)
                    if current_time < vehicle.estimated_time_of_departure and vehicle.battery.soc < vehicle.desired_soc:
                        # load vehicle
                        power += vehicle.battery.load(interval, ind_power)['avg_power']
                    elif vehicle.battery.soc < vehicle.desired_soc * 0.95:
                        # fail: desired SOC not reached
                        return idx, individual
            if power > gc_info['max_power']:
                # fail: too much power used
                return idx, individual
            else:
                fitness += util.get_cost(power, gc_info['cost'])

    for vehicles in vehicle_types:
        for vehicle in vehicles.values():
            if vehicle.battery.soc < vehicle.desired_soc * 0.95:
                # fail: desired SOC not reached after one day
                return idx, individual

    individual[1] = fitness

    return idx, individual

class Genetic(Strategy):
    """
    Charging strategy computed by genetic algorithm.
    """
    def __init__(self, constants, start_time, interval):
        super().__init__(constants, start_time, interval)
        self.description = "genetic"
        assert interval.seconds == 15*60, "Genetic algorithm only for 15 minute intervals"
        assert len(self.world_state.grid_connectors) == 1, "Only one Grid Connector allowed"

        self.INTERVALS = [1,1,2,4,8,16,16,32] # 24 hours
        # self.INTERVALS = [1,2,4,8,16,32,33]
        self.POPSIZE = 32
        self.GENERATIONS = 10
        self.N_CORE = mp.cpu_count()

        self.population = []

        # get main (and only) GC
        gc = list(self.world_state.grid_connectors.values())[0]
        # prepare dictionary of predicted external load
        self.pred_gc = {}

        timesteps_per_day = int(datetime.timedelta(days=1) / interval)
        cur_time = start_time
        for _ in range(timesteps_per_day):
            self.pred_gc[str(cur_time.time())] = {
                'ext_load': 0,
                'max_power': gc.max_power,
                'cost': gc.cost
            }
            cur_time += interval

    def set_fitness(self, result):
        self.population[result[0]] = result[1]

    def err_callback(self, err_msg):
        print('Callback error at parallel computing! The error message is: {}'.format(err_msg))

    def step(self, event_list=[]):
        super().step(event_list)

        # get main (and only) GC
        gc = list(self.world_state.grid_connectors.values())[0]

        # update predicted external load
        timestamp = str(self.current_time.time())
        predicted_load = self.pred_gc[timestamp]['ext_load']
        actual_load = sum(gc.current_loads.values())
        self.pred_gc[timestamp]['ext_load'] = 0.25 * predicted_load + 0.75 * actual_load
        gc_info = (gc.max_power, gc.cost)

        # peek into future events for grid op signals
        event_idx = 0
        timesteps_per_day = int(datetime.timedelta(days =1) / self.interval)
        timesteps_per_hour=     datetime.timedelta(hours=1) / self.interval
        cur_time = self.current_time - self.interval
        for _ in range(timesteps_per_day):
            cur_time += self.interval
            prediction = self.pred_gc[str(cur_time.time())]

            while True:
                try:
                    event = self.world_state.future_events[event_idx]
                except IndexError:
                    break
                if event.start_time > cur_time:
                    break
                event_idx += 1
                if type(event) == events.GridOperatorSignal:
                    # update gc info
                    cur_max_power = min(event.max_power or gc.max_power, gc.max_power)
                    gc_info[0] = cur_max_power
                    gc_info[1] = event.cost

            # update timeslot GC info
            prediction['max_power'] = gc_info[0]
            prediction['cost'] = gc_info[1]
            self.pred_gc[str(cur_time.time())] = prediction

        # reset charging station power
        for cs in self.world_state.charging_stations.values():
            cs.current_power = 0

        # gather current state of vehicles by vehicle type
        charging_stations = {}
        vehicle_types = []
        for type_name in sorted(self.world_state.vehicle_types.keys()):
            vtype = self.world_state.vehicle_types[type_name]
            vehicles = {}
            for vehicle_name, vehicle in self.world_state.vehicles.items():
                vehicle = self.world_state.vehicles[vehicle_name]
                cs_id = vehicle.connected_charging_station
                delta_soc = vehicle.desired_soc - vehicle.battery.soc
                if vehicle.vehicle_type == vtype and cs_id and delta_soc > 0:
                    vehicles[vehicle_name] = vehicle
                    charging_stations[cs_id] = 0
            vehicle_types.append(vehicles)

        num_vehicles = sum([len(vt) for vt in vehicle_types])
        if num_vehicles == 0:
            # no charging
            socs={vid: v.battery.soc for vid, v in self.world_state.vehicles.items()}
            return {'current_time': self.current_time, 'commands': {}, 'socs': socs}

        evaluated  = set()
        for idx, child in enumerate(self.population):
            # result from last timestep: shift by one
            child[0] = child[0][1:] + child[0][:1]
            child[1] = None
            self.population[idx] = child
            evaluated.add(fp(child[0]))

        for gen in range(self.GENERATIONS):

            # generate offspring
            children = []

            # only children not seen before allowed in population
            # set upper bound for maximum number of generated children
            # population may not be pop_size big (invalid individuals)
            for tries in range(1000 * self.POPSIZE):
                if (len(children) == self.POPSIZE):
                    # population full (pop_size new individuals)
                    break

                # get random parents from pop_size best results
                try:
                    [parent1, parent2] = random.sample(self.population, 2)
                    # crossover and mutate parents
                    # one point crossover
                    child = deepcopy(parent2)
                    child[1] = None
                    crossover = random.randint(0, len(parent1[0]))
                    for i in range(crossover):
                        child[0][i] = [g for g in parent1[0][i]]
                    # mutate
                    for x, ts_info in enumerate(child[0]):
                        for y, value in enumerate(ts_info):
                            value += random.gauss(-0.01, sigma=1/3)
                            value = max(value, 0)
                            value = min(value, 1 - sum(ts_info))
                            ts_info[y] = value
                        child[0][x] = ts_info
                except ValueError:
                    # not enough parents left / initial generation: generate random configuration
                    child = [[], None]
                    for _ in range(len(self.INTERVALS)):
                        ts_info = []
                        for _ in range(len(vehicle_types)):
                            value = round(random.random(), 2)
                            value = min(value, 1 - sum(ts_info))
                            ts_info.append(value)
                        child[0].append(ts_info)

                # check if child configuration has been seen before
                fingerprint = fp(child[0])
                if fingerprint not in evaluated:
                    # child config not seen so far
                    children.append(child)
                    # block, so not in population again
                    evaluated.add(fingerprint)
            else:
                print("Warning: number of retries exceeded. \
{} new configurations generated.".format(len(children)))

            if len(children) == 0:
                # no new children could be generated
                print("Aborting.")
                break

            # New population generated (parents + children)
            self.population += children
            self.population = self.population[:2 * self.POPSIZE]

            # evaluate generated population
            # open n_core worker threads
            pool = mp.Pool(processes=self.N_CORE)
            for idx, individual in enumerate(self.population):
                # print(individual)
                if individual[1] is None:  # not evaluated yet
                    pool.apply_async(
                        fitness_function, (idx, individual, deepcopy(vehicle_types), self.INTERVALS, self.pred_gc, self.current_time, self.interval),
                        callback=self.set_fitness,
                        error_callback=tb #self.err_callback
                    )
            pool.close()
            pool.join()

            # filter out individuals with invalid fitness values
            self.population = list(
                filter(lambda ind: ind[1] is not None, self.population))

            if len(self.population) == 0:
                # no configuration  was successful
                print(self.current_time, "No individuals left. Building new population.")
                continue

            self.population = sorted(self.population, key=lambda ind: ind[1])

            # next generation

        if len(self.population) == 0:
            raise Exception("GA failed")

        # get best result
        # first entry, first timestep in tupel
        best = self.population[0][0][0]
        delta_soc = 0

        power_left = gc.cur_max_power - actual_load
        for type_idx, vehicles in enumerate(vehicle_types):
            value = best[type_idx]
            for vehicle in vehicles.values():
                ind_power = value * power_left / len(vehicles)
                power = vehicle.battery.load(self.interval, ind_power)['avg_power']
                cs_id = vehicle.connected_charging_station
                charging_stations[cs_id] = gc.add_load(cs_id, power)

                assert vehicle.battery.soc <= 100
                assert vehicle.battery.soc >= 0, 'SOC of {} is {}'.format(vehicle_id, vehicle.battery.soc)

                delta_soc += vehicle.desired_soc - vehicle.battery.soc

        # print(self.current_time, self.population[0][1])
        print("{}: {}€, {} need charging, {} avg delta SOC".format(self.current_time, int(self.population[0][1]), num_vehicles, round(delta_soc / num_vehicles,2)))

        socs={vid: v.battery.soc for vid, v in self.world_state.vehicles.items()}
        return {'current_time': self.current_time, 'commands': charging_stations, 'socs': socs}
