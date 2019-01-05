import paho.mqtt.client as mqtt
import os
import atexit
import energenie
from energenie.Devices import MIHO013

energenie.init()


class Trv(MIHO013):
    def __init__(self, mqtt_client, device_id, air_interface=None):
        self.mqtt_client = mqtt_client
        MIHO013.__init__(self, device_id, air_interface)

    def handle_message(self, payload):
        result = super(Trv, self).handle_message(payload)
        self.mqtt_client.publish("home/spare_room/trv",
                                 "{\"temperature\": " + str(self.get_ambient_temperature())
                                 + ", \"voltage\": " + str(self.get_battery_voltage() or "null") + "}")
        return result


# The callback for when the client receives a CONNACK response from the server.
def on_connect(c, userdata, flags, rc):
    print("Connected with result code " + str(rc))

    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    c.subscribe("home/energenie/#")
    c.subscribe("home/spare_room/trv/set")


def handle_energenie(path, payload):
    house_code = int(path[2], 16)
    switch_idx = int(path[3])

    switch = energenie.Devices.ENER002((house_code, switch_idx))
    print("house_code: " + str(house_code) + " idx: " + str(switch_idx))

    if payload == "ON":
        print("Turning switch on")
        switch.turn_on()
    else:
        print("Turning switch off")
        switch.turn_off()


def handle_trv(path, payload):
    print("Setting trv to " + payload)
    target_temp = int(payload)
    valve.set_setpoint_temperature(target_temp)


handlers = {
    "energenie": handle_energenie,
    "spare_room": handle_trv
}


# The callback for when a PUBLISH message is received from the server.
def on_message(client, userdata, msg):
    payload = msg.payload.decode("utf-8")
    print(msg.topic + " - " + payload)

    path = str.split(msg.topic, "/")
    discriminator = path[1]

    handlers[discriminator](path, payload)


client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message

token = os.environ['MQTT_TOKEN']
client.username_pw_set("homeassistant", token)

client.connect("localhost", 1883, 60)
client.loop_start()

# spare_room_rad
valve = Trv(client, 8220)
energenie.fsk_router.add((4, 3, 8220), valve)


def onexit():
    print("Shutting down...")
    client.loop_stop()
    client.disconnect()
    energenie.finished()
    print("...done.")


atexit.register(onexit)

# Blocking call that processes network traffic, dispatches callbacks and
# handles reconnecting.
# Other loop*() functions are available that give a threaded interface and a
# manual interface.
while True:
    energenie.loop()
