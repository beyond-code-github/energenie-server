import paho.mqtt.client as mqtt
import os
import atexit
import energenie
from energenie.Devices import MIHO013

energenie.init()


class Trv(MIHO013):
    def handle_message(self, payload):
        result = super(Trv, self).handle_message(payload)
        print("Something extra")
        return result


# The callback for when the client receives a CONNACK response from the server.
def on_connect(client, userdata, flags, rc):
    print("Connected with result code " + str(rc))

    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    client.subscribe("home/#")


# The callback for when a PUBLISH message is received from the server.
def on_message(client, userdata, msg):
    payload = msg.payload.decode("utf-8")
    print(msg.topic + " - " + payload)

    path = str.split(msg.topic, "/")
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


client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message

token = os.environ['MQTT_TOKEN']
client.username_pw_set("homeassistant", token)

client.connect("localhost", 1883, 60)

# spare_room_rad
valve = Trv(8220)


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
    if not energenie.loop():
        client.loop()
