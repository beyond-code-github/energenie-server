import json
import os
import atexit
from functools import reduce

import energenie
import requests
import schedule
from energenie.Devices import MIHO013
from requests.auth import HTTPBasicAuth

from energenie_client import EnergenieClient
import logging

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
    response = requests.get(mihome_url, auth=HTTPBasicAuth(mihome_user, mihome_token))
    json_data = response.json()

    global mihome_data, mihome_reference
    mihome_data = json_data["data"]

    for trv in all_trvs:
        mihome_reference[trv.name] = mihome_reference.get(trv.name, trv.get_target_temperature())

        if mihome_reference[trv.name] != trv.get_target_temperature():
            logger.info("Target temperature for " + trv.name + " has changed to " + trv.get_target_temperature())

        trv.mqtt_client.publish("home/" + trv.name + "/trv/target", str(trv.get_target_temperature()), retain=True)

    update_call_for_heat()


class Trv(MIHO013):
    def __init__(self, name, mqtt_client, device_id, mihome_id, target_offset=0, air_interface=None):
        self.name = name
        self.mihome_id = mihome_id
        self.mqtt_client = mqtt_client
        self.target_offset = target_offset

        MIHO013.__init__(self, device_id, air_interface)
        self.voltageReadingPeriod = None
        self.diagnosticsReadingPeriod = None

        energenie.fsk_router.add((4, 3, device_id), self)

    def description(self):
        return self.name + " (" + str(self.get_ambient_temperature() or 99) + "/" + str(self.get_target_temperature()) + ")"

    def get_target_temperature(self):
        global mihome_data
        my_data = next(d for d in mihome_data if d["id"] == self.mihome_id)
        return float(my_data["target_temperature"]) + self.target_offset

    def handle_message(self, payload):
        result = super(Trv, self).handle_message(payload)
        self.mqtt_client.publish("home/" + self.name + "/trv/current", str(self.get_ambient_temperature()), retain=True)
        logger.info(self.name + " reports temperature " + str(self.get_ambient_temperature()))

        update_call_for_heat()

        return result


def update_call_for_heat():
    trvs_calling_for_heat = [
        trv for trv in all_trvs if (trv.get_ambient_temperature() or 99) < trv.get_target_temperature()]

    state = 'off'
    if len(trvs_calling_for_heat) > 0:
        state = 'on'
        logger.info("TRVs calling for heat: " + str([trv.description() for trv in trvs_calling_for_heat]))
    else:
        logger.info("No TRVs calling for heat: " + str([trv.description() for trv in all_trvs]))

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


def create_handler(trv):
    def handle_trv(payload, path):
        global mihome_reference

        logger.info("Setting " + trv.name + " to " + payload)

        mihome_url = "https://mihome4u.co.uk/api/v1/subdevices/set_target_temperature"
        json_data = "{\"id\":" + str(trv.mihome_id) + ", \"temperature\": " + payload + "}"
        logger.debug(json_data)

        response = requests.post(
            mihome_url,
            data={"params": json_data},
            auth=HTTPBasicAuth(mihome_user, mihome_token))

        logger.debug("Mihome response: " + response.status_code)
        logger.debug(response.text)

        mihome_reference[trv.name] = payload
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

all_trvs = [spare_room_valve, nursery_valve, living_room_1_valve, living_room_2_valve, bathroom_valve, master_bedroom_valve, hallway_valve]

handlers = reduce(
    lambda obj, item: { **obj, item.name : create_handler(item) },
    all_trvs, {"energenie": handle_energenie, "nest": handle_nest})


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
