import atexit
import logging
import math
import os
from functools import reduce

import energenie
import requests
import schedule
from energenie.Devices import MIHO013
from requests.auth import HTTPBasicAuth

from energenie_client import EnergenieClient

# create logger
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)

logger = logging.getLogger("energenie.server")
logger.setLevel(logging.INFO)
logger.addHandler(ch)

nest_temperature = None
mihome_data = None
mihome_reference = {}

mihome_user = os.environ['MIHOME_USER']
mihome_token = os.environ['MIHOME_TOKEN']


def fetch_mihome_data():
    logger.info("Fetching data from mihome gateway")
    mihome_url = "https://mihome4u.co.uk/api/v1/subdevices/list"

    try:
        response = requests.get(mihome_url, auth=HTTPBasicAuth(mihome_user, mihome_token))
        json_data = response.json()

        global mihome_data, mihome_reference
        mihome_data = json_data["data"]

        for trv in all_trvs:
            reference_temperature = mihome_reference.get(trv.name)

            if not reference_temperature:
                logger.info(
                    trv.name + " has no reference point yet. Target will be updated once the first reading comes through")
            else:
                if reference_temperature != trv.get_mihome_temperature():
                    mihome_reference[trv.name] = trv.get_mihome_temperature()
                    trv.set_target_temperature_from_mihome(mihome_reference[trv.name])
                    logger.info(
                        "Target temperature for " + trv.name + " has changed to " + str(trv.get_target_temperature()))

                trv.mqtt_client.publish("home/" + trv.name + "/trv/target", str(trv.get_target_temperature()),
                                        retain=True)

        update_call_for_heat()
    except Exception as e:
        logger.error("Error fetching mihome data: " + str(e))


class Trv(MIHO013):
    def __init__(self, name, mqtt_client, device_id, mihome_id, target_offset=0, air_interface=None):
        self.name = name
        self.mihome_id = mihome_id
        self.mqtt_client = mqtt_client
        self.target_offset = target_offset
        self.target_temperature = -99

        MIHO013.__init__(self, device_id, air_interface)
        self.voltageReadingPeriod = None
        self.diagnosticsReadingPeriod = None

        energenie.fsk_router.add((4, 3, device_id), self)

    def description(self):
        return self.name + " (" + str(self.get_ambient_temperature() or 99) + "/" + str(
            self.get_target_temperature()) + ")"

    def get_mihome_temperature(self):
        global mihome_data
        my_data = next(d for d in mihome_data if d["id"] == self.mihome_id)
        return float(my_data["target_temperature"])

    def get_target_temperature(self):
        return self.target_temperature

    def get_target_temperature_for_mihome(self):
        return math.floor(self.target_temperature - self.target_offset)

    def set_target_temperature(self, target):
        self.target_temperature = target

    def set_target_temperature_from_mihome(self, target):
        self.target_temperature = target + self.target_offset

    def is_calling_for_heat(self):
        return (self.get_ambient_temperature() or 99) < self.get_target_temperature()

    def update_state(self):
        state = "Off"
        initialised = self.get_target_temperature() > 0 and self.get_ambient_temperature() is not None

        if initialised:
            reference_temp = mihome_reference[self.name]

            if self.is_calling_for_heat():
                state = "Heat"
                adjusted_temp = self.get_target_temperature_for_mihome() + 1
                if adjusted_temp != reference_temp:
                    logger.info(self.name + " is calling for heat, setting mihome target temperature to " + str(
                        adjusted_temp) + "(+1)")
                    mihome_reference[self.name] = adjusted_temp
                    set_trv_temperature(self, adjusted_temp)
            else:
                adjusted_temp = self.get_target_temperature_for_mihome() - 1
                if adjusted_temp != reference_temp:
                    logger.info(self.name + " is not calling for heat, dropping mihome target temperature to " + str(
                        adjusted_temp) + "(-1)")
                    mihome_reference[self.name] = adjusted_temp
                    set_trv_temperature(self, adjusted_temp)

        self.mqtt_client.publish("home/" + self.name + "/trv/state", state, retain=True)

    def handle_message(self, payload):
        previous_reading = self.get_ambient_temperature()
        result = super(Trv, self).handle_message(payload)
        current_reading = self.get_ambient_temperature()

        self.mqtt_client.publish("home/" + self.name + "/trv/current", str(current_reading), retain=True)
        logger.info(self.name + " reports temperature " + str(current_reading))

        if current_reading and not previous_reading:
            mihome_temperature = self.get_mihome_temperature()
            mihome_reference[self.name] = mihome_temperature

            if current_reading > mihome_temperature:
                logger.info(self.name + " first reading is above mihome target, assuming trv is adjusted down")
                self.set_target_temperature_from_mihome(mihome_temperature + 1)
            else:
                logger.info(self.name + " first reading is below mihome target, assuming trv is adjusted up")
                self.set_target_temperature_from_mihome(mihome_temperature - 1)

        if current_reading != previous_reading:
            update_call_for_heat()

        return result


def update_call_for_heat():
    trvs_calling_for_heat = [trv for trv in all_trvs if trv.is_calling_for_heat()]

    state = 'off'
    if len(trvs_calling_for_heat) > 0:
        state = 'on'
        logger.info("TRVs calling for heat: " + str([trv.description() for trv in trvs_calling_for_heat]))
    else:
        logger.info("No TRVs calling for heat: " + str([trv.description() for trv in all_trvs]))

    for trv in all_trvs:
        trv.update_state()

    global client
    client.publish("home/nest/call_for_heat", state)


# The callback for when the client receives a CONNACK response from the server.
def on_connect(c, userdata, flags, rc):
    logger.info("Connected with result code " + str(rc))

    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    c.subscribe("home/energenie/#")
    c.subscribe("home/nest/temperature")

    for trv in all_trvs:
        c.subscribe("home/" + trv.name + "/trv/set")

    c.publish("constants/auto", "Auto", retain=True)


def handle_energenie(payload, path):
    house_code = int(path[2], 16)
    switch_idx = int(path[3])

    switch = energenie.Devices.ENER002((house_code, switch_idx))
    logger.info("house_code: " + str(house_code) + " idx: " + str(switch_idx))

    if payload == "ON":
        logger.info("Turning switch on")
        switch.turn_on()
    else:
        logger.info("Turning switch off")
        switch.turn_off()


def handle_nest(payload, path):
    global nest_temperature
    nest_temperature = payload
    logger.info("Nest reports temperature at " + nest_temperature)

    update_call_for_heat()


def biglight_on(device_id):
    mihome_url = "https://mihome4u.co.uk/api/v1/subdevices/power_on"
    json_data = "{\"id\":" + str(device_id) + "}"
    logger.debug(json_data)

    try:
        response = requests.post(
            mihome_url,
            data={"params": json_data},
            auth=HTTPBasicAuth(mihome_user, mihome_token))

        logger.debug("Mihome response: " + str(response.status_code))
        logger.debug(response.text)
    except Exception as e:
        logger.error("Error turning big light on: " + str(e))


def biglight_off(device_id):
    mihome_url = "https://mihome4u.co.uk/api/v1/subdevices/power_off"
    json_data = "{\"id\":" + str(device_id) + "}"
    logger.debug(json_data)

    try:
        response = requests.post(
            mihome_url,
            data={"params": json_data},
            auth=HTTPBasicAuth(mihome_user, mihome_token))

        logger.debug("Mihome response: " + str(response.status_code))
        logger.debug(response.text)
    except Exception as e:
        logger.error("Error turning big light off: " + str(e))


def biglight_brightness(device_id, level):
    mihome_url = "https://mihome4u.co.uk/api/v1/subdevices/set_dimmer_level"
    json_data = "{\"id\":" + str(device_id) + ", \"level\": " + str(level) + "}"
    logger.debug(json_data)

    try:
        response = requests.post(
            mihome_url,
            data={"params": json_data},
            auth=HTTPBasicAuth(mihome_user, mihome_token))

        logger.debug("Mihome response: " + str(response.status_code))
        logger.debug(response.text)
    except Exception as e:
        logger.error("Error turning big light off: " + str(e))


def handle_biglight(payload, path):
    device_id = int(path[2])
    command = (path[3])

    if command == "switch":
        if payload == "ON":
            logger.info("Turning big light on")
            biglight_on(device_id)
        else:
            logger.info("Turning big light off")
            biglight_off(device_id)

    if command == "brightness":
        logger.info("Setting big light brightness to " + str(payload))
        biglight_brightness(device_id, payload)


def set_trv_temperature(trv, temperature):
    mihome_url = "https://mihome4u.co.uk/api/v1/subdevices/set_target_temperature"
    json_data = "{\"id\":" + str(trv.mihome_id) + ", \"temperature\": " + str(temperature) + "}"
    logger.debug(json_data)

    try:
        response = requests.post(
            mihome_url,
            data={"params": json_data},
            auth=HTTPBasicAuth(mihome_user, mihome_token))

        logger.debug("Mihome response: " + str(response.status_code))
        logger.debug(response.text)
    except Exception as e:
        logger.error("Error setting trv temperature: " + str(e))


def create_handler(trv):
    def handle_trv(payload, path):
        logger.info("Handling set " + trv.name + " to " + payload)

        # We could receive a payload of 18, but mihome temp could already be 18
        # In this case, we would actually set mihome to 17 or 19 depending on current reading
        # We could also get a fractional value which mihome would not accept, so this would be the only way to make it
        # stick
        target = float(payload)
        trv.set_target_temperature(target)
        current_reading = trv.get_ambient_temperature()

        if current_reading > target:
            adjusted_temp = trv.get_target_temperature_for_mihome() - 1
            logger.info(trv.name + " current temperature is above new target, setting mihome temp to " + str(
                adjusted_temp) + "(-1)")

            # set mihome reference ahead of time so that we don't adjust it when data comes in
            mihome_reference[trv.name] = adjusted_temp
            set_trv_temperature(trv, adjusted_temp)
        else:
            adjusted_temp = trv.get_target_temperature_for_mihome() + 1
            logger.info(trv.name + " current temperature is below new target, setting mihome temp to " + str(
                adjusted_temp) + "(+1)")

            # set mihome reference ahead of time so that we don't adjust it when data comes in
            mihome_reference[trv.name] = adjusted_temp
            set_trv_temperature(trv, adjusted_temp)

        fetch_mihome_data()

    return handle_trv


energenie.init()
client = EnergenieClient()

spare_room_valve = Trv("spare_room", client, 8220, 183451, 1)
nursery_valve = Trv("nursery", client, 7746, 183449, 2)
living_room_1_valve = Trv("living_room_1", client, 8614, 190208)
living_room_2_valve = Trv("living_room_2", client, 7694, 190226)
bathroom_valve = Trv("bathroom", client, 7809536, 190529)
master_bedroom_valve = Trv("master_bedroom", client, 10496256, 190538)
hallway_valve = Trv("hallway", client, 10627584, 190566)

all_trvs = [spare_room_valve, nursery_valve, living_room_1_valve, living_room_2_valve, bathroom_valve,
            master_bedroom_valve, hallway_valve]

handlers = reduce(
    lambda obj, item: {**obj, item.name: create_handler(item)},
    all_trvs,
    {
        "energenie": handle_energenie,
        "nest": handle_nest,
        "biglight": handle_biglight
    })


# The callback for when a PUBLISH message is received from the server.
def on_message(client, userdata, msg):
    payload = msg.payload.decode("utf-8")
    logger.debug(msg.topic + " - " + payload)

    path = str.split(msg.topic, "/")
    discriminator = path[1]

    handlers[discriminator](payload, path)


schedule.every(30).seconds.do(fetch_mihome_data)
fetch_mihome_data()

client.on_connect = on_connect
client.on_message = on_message

token = os.environ['MQTT_TOKEN']
client.username_pw_set("homeassistant", token)
client.connect("localhost", 1883, 60)


def onexit():
    logger.info("Shutting down...")
    client.loop_stop()
    client.disconnect()
    energenie.finished()
    logger.info("...done.")


def on_loop():
    energenie.loop()
    schedule.run_pending()


atexit.register(onexit)

client.on_loop(on_loop)
client.loop_forever()
