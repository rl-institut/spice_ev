from copy import deepcopy
import datetime
import multiprocessing as mp
import random
import traceback
def tb(e):
    traceback.print_exception(type(e), e, e.__traceback__)

from netz_elog import events, util
from netz_elog.strategy import Strategy


def fp(individual):
    return hash(str(individual))
    # return str(individual)

def fitness_function(idx, individual, vehicles, intervals, pred_gc, start_time, interval):

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

            for v_idx, vehicle in enumerate(vehicles):
                value = ts_info[v_idx]
                ind_power = value * vehicle.battery.loading_curve.max_power
                if current_time < vehicle.estimated_time_of_departure and vehicle.battery.soc < vehicle.desired_soc:
                    # load vehicle
                    power += vehicle.battery.load(interval,ind_power)['avg_power']
                elif vehicle.battery.soc < vehicle.desired_soc * 0.95:
                    # fail: desired SOC not reached
                    return idx, individual

            if power > gc_info['max_power']:
                # fail: too much power used
                return idx, individual
            else:
                fitness += util.get_cost(power, gc_info['cost'])

    """
    for vehicle in vehicles.values():
        if vehicle.battery.soc < vehicle.desired_soc * 0.95:
            # fail: desired SOC not reached after one day
            return idx, individual
    """

    individual[1] = fitness

    return idx, individual

class Genetic(Strategy):
    """
    Charging strategy computed by genetic algorithm.
    """
    def __init__(self, constants, start_time, **kwargs):
        # defaults
        self.INTERVALS = [1,1,2,4,8,16,16,32] # 24 hours
        self.POPSIZE = 8# 32
        self.GENERATIONS = 2 #10
        self.N_CORE = mp.cpu_count()

        super().__init__(constants, start_time, **kwargs)
        self.description = "genetic"
        assert self.interval.seconds == 15*60, "Genetic algorithm only for 15 minute intervals"
        assert len(self.world_state.grid_connectors) == 1, "Only one Grid Connector allowed"

        if sum(self.INTERVALS) != 96:
            print("Warning: INTERVALS should add up to 24 hours (96 intervals)")

        self.population = []

        # get main (and only) GC
        gc = list(self.world_state.grid_connectors.values())[0]
        # prepare dictionary of predicted external load
        self.pred_gc = {}

        timesteps_per_day = int(datetime.timedelta(days=1) / self.interval)
        cur_time = start_time
        for _ in range(timesteps_per_day):
            self.pred_gc[str(cur_time.time())] = {
                'ext_load': 0,
                'max_power': gc.max_power,
                'cost': gc.cost
            }
            cur_time += self.interval

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

        # gather all vehicles currently in need of charge
        vehicles = []
        charging_stations = {}
        for vehicle_id in sorted(self.world_state.vehicles.keys()):
            vehicle = self.world_state.vehicles[vehicle_id]
            cs_id = vehicle.connected_charging_station
            if vehicle.get_delta_soc() > 0 and cs_id:
                vehicles.append(vehicle)
                charging_stations[cs_id] = 0

        if not(vehicles):
            # no charging
            socs={vid: v.battery.soc for vid, v in self.world_state.vehicles.items()}
            return {'current_time': self.current_time, 'commands': {}, 'socs': socs}

        evaluated  = set()
        self.population = []

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
                    # mutate. On average, value will be lower
                    for x, ts_info in enumerate(child[0]):
                        for y, value in enumerate(ts_info):
                            value += random.gauss(-0.01, sigma=1/3)
                            value = max(value, 0)
                            value = min(value, 1)
                            ts_info[y] = value
                        child[0][x] = ts_info
                except ValueError:
                    # not enough parents left / initial generation: generate random configuration
                    child = [[], None]
                    for _ in range(len(self.INTERVALS)):
                        ts_info = []
                        for _ in range(len(vehicles)):
                            value = random.random()
                            # value = round(value, 2)
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
                        fitness_function, (idx, individual, deepcopy(vehicles), self.INTERVALS, self.pred_gc, self.current_time, self.interval),
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
        for v_idx, vehicle in enumerate(vehicles):
            value = best[v_idx]
            ind_power = value * vehicle.battery.loading_curve.max_power
            power = vehicle.battery.load(self.interval, ind_power)['avg_power']
            cs_id = vehicle.connected_charging_station
            charging_stations[cs_id] = gc.add_load(cs_id, power)

            assert vehicle.battery.soc <= 100
            assert vehicle.battery.soc >= 0, 'SOC of {} is {}'.format(vehicle_id, vehicle.battery.soc)

            delta_soc += vehicle.get_delta_soc()

        # print(self.current_time, self.population[0][1])
        print("{}: {}€, {} need charging, {} avg delta SOC".format(self.current_time, int(self.population[0][1]), len(vehicles), round(delta_soc / len(vehicles),2)))

        socs={vid: v.battery.soc for vid, v in self.world_state.vehicles.items()}
        return {'current_time': self.current_time, 'commands': charging_stations, 'socs': socs}
