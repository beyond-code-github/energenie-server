import json
import os
import atexit
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

mihome_user = os.environ['MIHOME_USER']
mihome_token = os.environ['MIHOME_TOKEN']


def fetch_mihome_data():
    logger.info("Fetching data from mihome gateway")
    mihome_url = "https://mihome4u.co.uk/api/v1/subdevices/list"
    response = requests.get(mihome_url, auth=HTTPBasicAuth(mihome_user, mihome_token))
    json_data = response.json()

    global mihome_data
    mihome_data = json_data["data"]

    for trv in all_trvs:
        trv.mqtt_client.publish("home/" + trv.name + "/trv/target", str(trv.get_target_temperature()), retain=True)


class Trv(MIHO013):
    def __init__(self, name, mqtt_client, device_id, mihome_id, air_interface=None):
        self.name = name
        self.mihome_id = mihome_id
        self.mqtt_client = mqtt_client

        MIHO013.__init__(self, device_id, air_interface)
        self.voltageReadingPeriod = None
        self.diagnosticsReadingPeriod = None

    def description(self):
        return self.name + " (" + str(self.get_ambient_temperature() or 99) + "/" + str(self.get_target_temperature()) + ")"

    def get_target_temperature(self):
        global mihome_data
        my_data = next(d for d in mihome_data if d["id"] == self.mihome_id)
        return my_data["target_temperature"]

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

    #self.mqtt_client.publish("home/nest/call_for_heat", state)


# The callback for when the client receives a CONNACK response from the server.
def on_connect(c, userdata, flags, rc):
    logger.info("Connected with result code " + str(rc))

    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    c.subscribe("home/energenie/#")
    c.subscribe("home/nest/temperature")

    c.subscribe("home/spare_room/trv/set")
    c.subscribe("home/nursery/trv/set")
    c.subscribe("home/living_room_1/trv/set")
    c.subscribe("home/living_room_2/trv/set")


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


def create_handler(trv):
    def handle_trv(payload, path):
        logger.info("Setting " + trv.name + " to " + payload)
        target_temp = float(payload)

        mihome_url = "https://mihome4u.co.uk/api/v1/subdevices/set_target_temperature"
        json_data = "{\"id\":" + trv.mihome_id + ", \"temperature\": " + str(target_temp) + "}"

        response = requests.post(
            mihome_url,
            data={"params": json_data},
            auth=HTTPBasicAuth(mihome_user, mihome_token))

        logger.debug("Mihome response: " + response.status_code)
        logger.debug(response.text)

    return handle_trv


energenie.init()
client = EnergenieClient()

spare_room_valve = Trv("spare_room", client, 8220, 183451)
energenie.fsk_router.add((4, 3, 8220), spare_room_valve)

nursery_valve = Trv("nursery", client, 7746, 183449)
energenie.fsk_router.add((4, 3, 7746), nursery_valve)

living_room_1_valve = Trv("living_room_1", client, 8614, 190208)
energenie.fsk_router.add((4, 3, 8614), living_room_1_valve)

living_room_2_valve = Trv("living_room_2", client, 7694, 190226)
energenie.fsk_router.add((4, 3, 7694), living_room_2_valve)

all_trvs = [spare_room_valve, nursery_valve, living_room_1_valve, living_room_2_valve]

handlers = {
    "energenie": handle_energenie,
    "nest": handle_nest,
    "spare_room": create_handler(spare_room_valve),
    "nursery": create_handler(nursery_valve),
    "living_room_1": create_handler(living_room_1_valve),
    "living_room_2": create_handler(living_room_2_valve),
}


# The callback for when a PUBLISH message is received from the server.
def on_message(client, userdata, msg):
    payload = msg.payload.decode("utf-8")
    logger.debug(msg.topic + " - " + payload)

    path = str.split(msg.topic, "/")
    discriminator = path[1]

    handlers[discriminator](payload, path)


schedule.every(1).minutes.do(fetch_mihome_data)
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
