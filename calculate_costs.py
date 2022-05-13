#!/usr/bin/env python3
import argparse
import csv
import json
import datetime

simulation_data_path = 'examples/simulation.csv'
simulation_cfg_path = 'examples/simulate.cfg'
preisblatt_slp_path = 'src/Preisblatt_SLP.json'
preisblatt_lg_jlp_path = 'src/Preisblatt_LG_JLP.json'
costs_variable_path = 'src/cost_parameter_variable.json'

timestep_s = 15 * 60 #todo: austauschen

def read_simulation_csv(csv_file):
    price_list = []  # [€/kWh]
    power_grid_supply_list = []  # [kW]
    power_feed_in_list = []  # [kW]
    data_dict = {}
    with open(csv_file) as simulation_data:
        reader = csv.reader(simulation_data, delimiter=",")
        next(reader, None) # skip the header
        for row in reader:

            # find value for parameter:
            price = float(row[2])
            power_grid_supply = float(row[3])
            power_feed_in = float(row[5])

            # append value to the respective list:
            price_list.append(price)
            power_grid_supply_list.append(power_grid_supply)
            power_feed_in_list.append(power_feed_in)

        #data_dict['price']=price_list
        #dict oder Listen übergeben?

    #return data_dict
    return price_list, power_grid_supply_list, power_feed_in_list
    #todo: benötigte Listen und dazugehrige Spalten werden noch strategieabhängig --> Abhängigkeit ergänzen
    #todo: price_list raus

def get_strategy(cfg_path):
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

            if k == 'strategy':
                strategy = v
    return strategy

def find_prizes(jahresbenutzungsdauer, energy_supply_per_year):
    # todo: Spannungsebene integrieren. woher?
    if energy_supply_per_year > 100000 and jahresbenutzungsdauer < 2500:
        type = 'jlp'  # evtl raus
        #print('Typ 1: ' + type + '\n' + 'Nutzungsdauer: ' + str(jahresbenutzungsdauer))
        with open(preisblatt_lg_jlp_path) as f:
            preisblatt_lg_jlp = json.load(f)
        arbeitspreis = preisblatt_lg_jlp['<2500 h/a']['Arbeitspreis ct/kWh']['MS']
        leistungspreis = preisblatt_lg_jlp['<2500 h/a']['Leistungspreis EUR/kW*a']['MS']
    elif energy_supply_per_year > 100000 and jahresbenutzungsdauer >= 2500:
        type = 'jlp'  # evtl raus
        #print('Typ 2: ' + type + '\n' + 'Nutzungsdauer: ' + str(jahresbenutzungsdauer))
        with open(preisblatt_lg_jlp_path) as f:
            preisblatt_lg_jlp = json.load(f)
        arbeitspreis = preisblatt_lg_jlp['>=2500 h/a']['Arbeitspreis ct/kWh']['MS']
        leistungspreis = preisblatt_lg_jlp['>=2500 h/a']['Leistungspreis EUR/kW*a']['MS']
    else:  # energy_supply_per_year <= 100000:
        type = 'slp'  # evtl raus
        #print('Typ 3: ' + type + '\n' + 'Nutzungsdauer: ' + str(jahresbenutzungsdauer))
        with open(preisblatt_slp_path) as f:
            preisblatt_slp = json.load(f)
        arbeitspreis = preisblatt_slp['Arbeitspreis ct/kWh']['Nettopreis']
        leistungspreis = preisblatt_slp['Grundpreis EUR/a']['Nettopreis']  # eigtl grundpreis # todo:brutto oder netto?
    return arbeitspreis, leistungspreis

def calculate_costs(strategy, power_gs_lst, price_lst,flag_cost_calc):
    if flag_cost_calc == True:

        # TEMPORAL PARAMETERS:
        number_timestamps = len(power_gs_lst) # durch Spalte mir Zeitstempeln ersetzen?
        number_list = list(range(number_timestamps)) # vielleicht erst später im Code
        duration_sim_s = (number_timestamps - 1) * timestep_s
        duration_year_s = 365 * 24 * 60 * 60

        # ENERGY SUPPLY:
        energy_supply_sim = sum(power_gs_lst) * timestep_s / 3600
        energy_supply_per_year = energy_supply_sim * (duration_year_s / duration_sim_s)

        # PRICES AND COSTS FOR POWER:
        if strategy == 'greedy':
            #energy_supply_per_year = 25000 # nur Platzhalter, später raus [kWh/a]
            max_power_grid_supply = min(power_gs_lst)  # minimum wegen negativen Werten [kW]
            jahresbenutzungsdauer = abs(energy_supply_per_year / max_power_grid_supply) #[h/a]
            arbeitspreis, leistungspreis = find_prizes(jahresbenutzungsdauer, energy_supply_per_year)

            costs_power_per_year_eur = leistungspreis
            costs_power_sim_eur = costs_power_per_year_eur / (duration_year_s / duration_sim_s)
            # print('pro Jahr: ' + str(costs_power_per_year_eur))
            # print('pro Sim: ' + str(costs_power_sim_eur))

            price_list = []
            for element in power_gs_lst:
                price_list.append(arbeitspreis)

        elif strategy == 'balanced_market':
            print(strategy)
            '''
            METHODIK:
            
            #max_power_for_costs =  ...       #todo: max. power nur aus entspr. Zeitfenstern?
            #jahresbenutzungsdauer = abs(energy_supply_per_year / max_power_for_costs)  # [h/a] 
            #arbeitspreis, leistungspreis = find_prizes(jahresbenutzungsdauer, energy_supply_per_year)
            
            #costs_power_per_year_eur = leistungspreis * max_power_for_costs

            arbeitspreis_1 = arbeitspreis * 0.68
            arbeitspreis_2 = arbeitspreis * 1
            arbeitspreis_3 = arbeitspreis * 1.5

            price_list = []
            for element in power_gs_lst:
                if #Stufe1:
                    price_list.append(arbeitspreis_1)
                elif # Stufe 2:
                    price_list.append(arbeitspreis_2)
                elif #Stufe 3:
                    price_list.append(arbeitspreis_3)
                else:
                    #Fehlerabfang
            '''
        elif strategy == 'flex_window':
            print(strategy)
            #Monatsleistungspreis!
            #max_power_for_costs =
            #costs_power_per_year_eur = leistungspreis * max_power_for_costs #wording

        else:
            print('choose different strategy')
            #todo: Abbruch und Warnung integrieren

        # COSTS FOR ENERGY:
        costs_energy_sim_eur = 0 #variable gets updates with every timestep
        energy_supply_per_timestep_list = []  # [kWh]

        #create lists with energy supply per timestep in order to calculate the energy costs:
        for number in number_list:
            energy_supply_per_timestep = power_gs_lst[number] * timestep_s / 3600
            energy_supply_per_timestep_list.append(energy_supply_per_timestep) # wird die Liste überhaupt benötigt?
            costs_energy_sim_eur = costs_energy_sim_eur + (energy_supply_per_timestep * price_list[number])  # negativ bei Netzbezug

        costs_energy_per_year_eur = costs_energy_sim_eur * (duration_year_s / duration_sim_s)
        # todo: Einheiten bei Preisen berücksichtigen!!!

        energy_supply_simulation = sum(energy_supply_per_timestep_list)

        '''  
        #EEG and V2G:
        
        # EEG und V2G prizes:
        with open(costs_variable_path) as f:
            costs_variable = json.load(f)
            
        #feed-in bei PV abh. von peakleistung der PV-Anlage. Wie wird dann mit V2G umgegangen?
        #pv_kwp =
        
        #RLM:
        
        #TOTAL:  
        costs_total_sim_eur = costs_energy_sim_eur + costs_power_sim_eur #+...
        costs_total_per_year_eur = costs_energy_per_year_eur #+ costs_power_per_year_eur + costs_rlm_eur + costs_eeg + costs_v2g +---
        print('---RESULTS---')
        print('total costs per year: ' + str(costs_total_per_year_eur) + ' Eur')
        '''

if __name__ == '__main__':
    run_cost_calc = True

    #todo: parser kann eigtl wieder raus
    parser = argparse.ArgumentParser()
    #parser.add_argument('--price', help="list with energy price per timestep", type=list)
    #parser.add_argument('--power_gs', help="list with power supplied from the grid per timestep", type=list)
    #parser.add_argument('--power_fi', help="list with power fed into the grid per timestep", type=list)
    parser.add_argument('--strategy', help="charging strategy for electric vehicles", type=str)
    #parser.add_argument('--config', help='Use config file to set arguments') # nur mit cfg

    args = parser.parse_args()

    strategy = get_strategy(simulation_cfg_path)

    price_list, power_grid_supply_list, power_feed_in_list = read_simulation_csv(simulation_data_path)

    z = calculate_costs(strategy, power_grid_supply_list, price_list, flag_cost_calc=True) #todo: price_list wieder raus



