import os
from subprocess import Popen

"""
Alle betrachteten Preissignale
"""
PREIS_UNVARIABEL = 'Preissignale/Price_Netz_Unvariabel.csv'
PREIS_UHRZEIT = 'Preissignale/Price_Netz_Uhrzeit.csv'
PREIS_NETZ = 'Preissignale/Price_Netz_Gesamt.csv'
PREIS_NETZ_1 = 'Preissignale/Price_Netz_Gesamt_01.csv'
PREIS_NETZ_5 = 'Preissignale/Price_Netz_Gesamt_05.csv'
PREIS_NETZ_10 = 'Preissignale/Price_Netz_Gesamt_10.csv'
PREIS_NETZ_20 = 'Preissignale/Price_Netz_Gesamt_20.csv'
PREIS_NETZ_AP = 'Preissignale/Price_Netz_Gesamt_ohne_LP.csv'
PREIS_STROMNEV = 'Preissignale/Price_Netz_Stromnev19_sim.csv'
PREIS_NSM_1 = 'Preissignale/Price_NSM_1.csv'
PREIS_NSM_4 = 'Preissignale/Price_NSM_4.csv'
PREIS_NSM_5 = 'Preissignale/Price_NSM_5.csv'
PREIS_NSM_7 = 'Preissignale/Price_NSM_7.csv'
PREIS_NSM_68 = 'Preissignale/Price_NSM_68.csv'
PREIS_NSM_75 = 'Preissignale/Price_NSM_75.csv'

def generate(bezeichner, preissignal: str, preis_col='"Gesamtpreis [EUR/kWh]"'):
    name = f"{bezeichner}_{preissignal.replace('.csv', '').replace('Preissignale/', '')}"
    args = f"--include-price-csv {preissignal} --include-price-csv-option column {preis_col} --include-price-csv-option step_duration_s 900"
    os.system(
        f"python generate.py vNN/simulations/{name}.json --config vNN/dhl_fuhrpark.cfg {args}")
    print(name, " erfolgreich generiert")


def generate_all():
    """
    Erstellt für alle angegebenen Preissignale eine reine Netzentgeltoptimierungs-Simulation und eine mit dem Börsenpreis zusätzlich
    """
    for preissignal in [PREIS_UNVARIABEL, PREIS_STROMNEV, PREIS_UHRZEIT, PREIS_NETZ, PREIS_NETZ_1, PREIS_NETZ_5, PREIS_NETZ_10,
                        PREIS_NETZ_20, PREIS_NETZ_AP, PREIS_NSM_7, PREIS_NSM_1, PREIS_NSM_4, PREIS_NSM_5, PREIS_NSM_68, PREIS_NSM_75]:
        generate("Basis", preissignal)
        generate("Boerse", preissignal, preis_col='"Gesamtpreis Boerse [EUR/kWh]"')


generate_all()

processes = []
for filename in os.listdir('vNN/simulations'):
    """
    Simuliert alle erstellten Simulations-Dateien nach dem Market-Balanced Algorithmus
    """
    if ".json" not in filename:
        continue
    sim_command = ['python', 'simulate.py', f'vNN/simulations/{filename}', '--config',
                   'vNN/sim_balanced_market.cfg']
    if "Boerse" in filename:
        sim_command.append('--strategy-option')
        sim_command.append('HORIZON')
        sim_command.append('24')
        sim_command.append('--strategy-option')
        sim_command.append('SIGNALTIME')
        sim_command.append('12')

    elif "Uhrzeit" in filename or "Stromnev" in filename:
        sim_command.append('--strategy-option')
        sim_command.append('HORIZON')
        sim_command.append('72')
    else:
        sim_command.append('--strategy-option')
        sim_command.append('HORIZON')
        sim_command.append('24')
        sim_command.append('--strategy-option')
        sim_command.append('SIGNALTIME')
        sim_command.append('11')

    sim_command.append('--save-timeseries')
    sim_command.append(f"vNN/results/{filename.replace('.json', '.csv').replace('19_sim', '19')}")
    processes.append(Popen(sim_command))
    print(filename, "Sim Gestartet")

for process in processes:
    process.wait()
