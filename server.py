import paho.mqtt.client as mqtt
import os
import atexit
import energenie

energenie.init()


# The callback for when the client receives a CONNACK response from the server.
def on_connect(client, userdata, flags, rc):
    print("Connected with result code " + str(rc))

    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    client.subscribe("home/energenie/*/*")


# The callback for when a PUBLISH message is received from the server.
def on_message(client, userdata, msg):
    path = str.split(msg.topic)
    house_code = path[2]
    switch_idx = path[3]

    switch = energenie.Devices.ENER002((house_code, switch_idx))

    if msg.payload == 'ON':
        switch.turn_on()
    else:
        switch.turn_off()

    print(msg.topic + " - " + str(msg.payload))


client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message

token = os.environ['MQTT_TOKEN']
client.username_pw_set("homeassistant", token)

client.connect("localhost", 1883, 60)

# Blocking call that processes network traffic, dispatches callbacks and
# handles reconnecting.
# Other loop*() functions are available that give a threaded interface and a
# manual interface.
client.loop_forever()


def onexit():
    print("Shutting down...")
    client.loop_stop()
    client.disconnect()
    energenie.finished()
    print("...done.")


atexit.register(onexit)
