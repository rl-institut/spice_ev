#!/usr/bin/env python3
import argparse
import csv
import datetime

from src.util import set_options_from_config
import csv

simulation_data_path = 'examples/simulation.csv'
strategy = 'greedy' #todo: austauschen, aus generate.cfg. beziehen
timestep_s = 15 * 60 # austauschen
#arbeitspreis = 0

def read_simulation_csv(csv_file):
    price_list = []  # [€/kWh]
    power_grid_supply_list = []  # [kW]
    power_feed_in_list = []  # [kW]
    data_dict = {}
    with open(csv_file) as simulation_data:
        reader = csv.reader(simulation_data, delimiter=",")
        next(reader, None) # skip the header
        for row in reader:
            #find value for parameter:
            price = float(row[2])
            power_grid_supply = float(row[3])
            power_feed_in = float(row[5])
            #append value to the respective list:
            price_list.append(price)
            power_grid_supply_list.append(power_grid_supply)
            power_feed_in_list.append(power_feed_in)
        #data_dict['price']=price_list
        #dict oder Listen übergeben?
    #return data_dict
    return price_list, power_grid_supply_list, power_feed_in_list
    #todo: benötigte Listen und dazugehrige Spalten strategieabhängig --> Abhängigkeit ergänzen

def calculate_costs(power_gs_lst, price_lst):
    arbeitspreis = 0
    energy_supply_per_timestep_list = []  # [kWh]
    number_timestamps = len(power_gs_lst) # durch Spalte mir Zeitstempeln ersetzen?
    number_list = list(range(number_timestamps))
    #create lists with energy supply per timestep in order to calculate the energy costs:
    for number in number_list:
        energy_supply_per_timestep = power_gs_lst[number] * timestep_s / 3600
        arbeitspreis = arbeitspreis + (energy_supply_per_timestep * price_lst[number]) #negativ bei Netzbezug
        energy_supply_per_timestep_list.append(energy_supply_per_timestep) # wird die Liste überhaupt benötigt?

    ### SLP ###
    # ARBEITSPREIS SLP:
    #if strategy == 'greedy':
        #arbeitspreis = arbeitspreis + (energy_supply_per_timestep * price)

def read_from_cfg(args): #Test, später raus
    if type(args) == argparse.Namespace:
        # cast arguments to dictionary for default handling
        args = vars(args)
    strategy_name = args.get("strategy", "greedy")
    return strategy_name

def test_fkt(flag_cost_calc):
    print('Dies ist ein Test')
    if flag_cost_calc == True:
        print('Kostenkalkulation wird durchgeführt')
'''
def test_fkt_2(variablen):
    parser2 = argparse.ArgumentParser()
    parser2.add_argument('--v1', help="charging strategy for electric vehicles", type=str)
    variablen = parser2.parse_args()

    #print(variablen.strategy)
    print('Test')
v1 = 'fdsf'
test2 = test_fkt_2(v1)
'''
#a,b,c = read_simulation_csv(simulation_data_path)
#print(c)
#print(c['price'])

def getArgs(argv=None):
    parser = argparse.ArgumentParser()
    #group = parser.add_mutually_exclusive_group()
    #group.add_argument('--testv', type =int)
    #parser.add_argument('--price', help="list with energy price per timestep", type=list)
    #parser.add_argument('--power_gs', help="list with power supplied from the grid per timestep", type=list)
    # parser.add_argument('--power_fi', help="list with power fed into the grid per timestep", type=list)
    # parser.add_argument('--strategy', help="charging strategy for electric vehicles", type=str)
    # parser.add_argument('--config', help='Use config file to set arguments')
    parser.add_argument('--testv', type=int)

    return parser.parse_args(argv)

if __name__ == '__main__':
    run_cost_calc = True
    argvals=None
    #argvals = '6'
    testv = 6
    #args = getArgs(--testv 6)
    args = getArgs(argvals)
    print(args)
    '''
    parser = argparse.ArgumentParser()
    parser.add_argument('--price', help="list with energy price per timestep", type=list)
    parser.add_argument('--power_gs', help="list with power supplied from the grid per timestep", type=list)
    #parser.add_argument('--power_fi', help="list with power fed into the grid per timestep", type=list)
    #parser.add_argument('--strategy', help="charging strategy for electric vehicles", type=str)
    #parser.add_argument('--config', help='Use config file to set arguments')

    args = parser.parse_args()
    '''
    with open('examples/simulate.cfg', 'r') as f:
        for line in f:
            line = line.strip()
            if line.startswith('#'):
                # comment
                continue
            if len(line) == 0:
                # empty line
                continue
            k, v = line.split('=')
            k = k.strip()
            v = v.strip()
            '''
            try:
                # option may be special: number, array, etc.
                v = json.loads(v)
            except ValueError:
                # or not
                pass
            # known option?
            if (k not in args) and check:
                raise ValueError("Unknown option {}".format(k))
            # set option
            vars(args)[k] = v
            '''
            if k == 'strategy':
                strategy = v
                print(strategy)

    #test_fkt_3(price=[1,2])
    #print(args.price)