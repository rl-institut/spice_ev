import datetime as datetime
import json
import csv

# DEFAULT VALUES COOLING:
C_FACTOR = 12.5
C_POWER = 3
MINUTES_PAUSE_CONSUMPTION = 2
HEATING_LIMIT = 17.5
COOLING_LIMIT = 26
H_PATH = "./examples/heating_consumption.csv"

DEFAULT_TEMPERATURE = {"winter": {
    "00:00:00": -1.1,
    "01:00:00": -1.1,
    "02:00:00": -2.2,
    "03:00:00": -2.7,
    "04:00:00": -2.7,
    "05:00:00": -2.8,
    "06:00:00": -2.8,
    "07:00:00": -3.8,
    "08:00:00": -3.8,
    "09:00:00": -3.9,
    "10:00:00": -3.9,
    "11:00:00": -2.7,
    "12:00:00": -1.1,
    "13:00:00": 1.1,
    "14:00:00": 2.2,
    "15:00:00": 2.2,
    "16:00:00": 1.1,
    "17:00:00": 0,
    "18:00:00": 0,
    "19:00:00": -1.1,
    "20:00:00": -1.1,
    "21:00:00": -1.1,
    "22:00:00": -1.1,
    "23:00:00": -1.1
},
    "summer": {
    "00:00:00": 23.8,
    "01:00:00": 23.8,
    "02:00:00": 22.7,
    "03:00:00": 22.2,
    "04:00:00": 21.1,
    "05:00:00": 20,
    "06:00:00": 20,
    "07:00:00": 21.1,
    "08:00:00": 21.7,
    "09:00:00": 25,
    "10:00:00": 28.8,
    "11:00:00": 31.1,
    "12:00:00": 32.7,
    "13:00:00": 33.8,
    "14:00:00": 36.1,
    "15:00:00": 36.1,
    "16:00:00": 36.1,
    "17:00:00": 33.9,
    "18:00:00": 32.2,
    "19:00:00": 32.2,
    "20:00:00": 32.2,
    "21:00:00": 25,
    "22:00:00": 23.9,
    "23:00:00": 25
}
}


def calculate_trip_consumption(trip, vehicle_dict, vehicle_type, rush_hour=None):
    """
    Calculates delta_soc and energy consumption for one trip

    :param trip: dictionary with trip information (see note for further explanation)
    :type trip: dict
    :param vehicle_dict: dictionary with information about the specific vehicle type, especially \
    capacity, mileage, hc
    :type vehicle_dict: dict
    :param vehicle_type: name of the specific vehicle type
    :type vehicle_type: str
    :param rush_hour: dict containing hours of rush hours, see
    example/generate_opp_from_schedule.cfg for further information.
    :type rush_hour: dict
    :return: delta_soc, total_consumption
    :rtype: tuple

    note: A trip should contain the following information:
    trip = {distance,\
            mileage,\
            departure_time, (optional, needs to be set if you want to use rush_hour)\
            arrival_time, (optional, needs to be set if you want to use rush_hour)}\
    """
    # calculate mileage
    get_time = "2018-01-01 12:00:00"
    time_dt = datetime.datetime.strptime(get_time, '%Y-%m-%d %H:%M:%S')
    traffic = "fluent"
    if rush_hour:
        # check if time is in rush_hour and if yes, get according traffic
        for key in rush_hour.keys():
            for times in rush_hour[key]:
                in_rh = in_time_period(datetime.time(int(times[0].split(":")[0]),
                                                     int(times[0].split(":")[1])),
                                       datetime.time(int(times[1].split(":")[0]),
                                                     int(times[1].split(":")[1])), time_dt.time())
                if in_rh:
                    traffic = key
                    break
    # calculate consumption of the drive
    hc = vehicle_dict["hc"]
    vt = vehicle_type.split("_")[0]
    if type(trip["mileage"]) == str:
        if hc != "winter" and hc != "summer":
            print("hc is not defined as 'summer' or 'winter', thus the default energy consumption "
                  "'no hc' is taken. If you want to define a different mileage, enter your number "
                  " in 'mileage'.")
            hc = "else"
        try:
            with open(trip["mileage"]) as json_file:
                ec = json.load(json_file)
        except FileNotFoundError:
            print("The energy consumption file in 'mileage' is not recognized. "
                  "Please insert a numeric value or a valid json file path.")
        if ec[vt]["energy_type"] == "Diesel":
            consumption_drive = ((ec[vt][hc][traffic] * 0.3 * 10) * ec[vt]["factor"]) \
                                / (0.99 * 0.99) * float(trip["distance"])
        else:
            print("Only the usage of Diesel is implemented yet.")

    # calculate heating and/or cooling
    cooling = 0
    heating = 0
    if hc and hc != "winter" and hc != "summer":
        print("The option to insert your own temperature time series is not implemented yet.")
    if hc == "winter" or hc == "summer":
        temp_dict = DEFAULT_TEMPERATURE[hc]
        # get temperature from default temperature dicts
        time = str(time_dt.time())
        time_h = datetime.datetime.strptime(time, '%H:%M:%S')
        closest_time = min(list(temp_dict.keys()), key=lambda t:
                           abs(time_h - datetime.datetime.strptime(t, "%H:%M:%S")))
        temp = temp_dict[closest_time]

        if int(trip["pause"]) > MINUTES_PAUSE_CONSUMPTION:
            minutes_pause = MINUTES_PAUSE_CONSUMPTION
        else:
            minutes_pause = int(trip["pause"])

        if temp > COOLING_LIMIT:
            cooling_drive = consumption_drive * C_FACTOR / 100 * float(trip["distance"])
            cooling_pause = minutes_pause / 60 * C_POWER
            cooling = cooling_drive + cooling_pause

        if temp < HEATING_LIMIT:
            heating_dict = csv_to_dict(H_PATH)
            # get nearest temperature in heating dict
            temps = [float(t["temperature"]) for t in heating_dict]
            nearest_temp = min(list(temps), key=lambda t: abs(temp - t))
            heating_drive = float([item[traffic] for item in heating_dict if item["temperature"] ==
                                   str(nearest_temp)][0]) * float(trip["distance"])

            h_power = float([item["dense"] for item in heating_dict if item["temperature"] ==
                            str(nearest_temp)][0]) * float(heating_dict[1]["speed"])
            heating_pause = (minutes_pause / 60) * h_power

            heating = heating_drive + heating_pause

    total_consumption = consumption_drive + cooling + heating

    delta_soc = - (total_consumption / vehicle_dict["capacity"])

    return delta_soc, total_consumption


def in_time_period(start_time, end_time, time):
    """
    Returns true if time between start_time and end_time.

    :param start_time: start time
    :param end_time: end time
    :param time: time
    :return: bool
    """
    if start_time < end_time:
        return time >= start_time and time <= end_time
    else:
        # Over midnight:
        return time >= start_time or time <= end_time


def csv_to_dict(csv_path):
    """
    Reads csv file and returns a dictionary

    :param csv_path: str
    :param headers: bool
    :return: dict
    """
    dict = []
    with open(csv_path, 'r') as file:
        reader = csv.reader(file)
        columns = next(reader)
        for row in reader:
            row_data = {}
            for i in range(len(row)):
                row_key = columns[i].lower()
                row_data[row_key] = row[i]
            dict.append(row_data)
    return dict
