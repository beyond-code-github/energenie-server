from paho.mqtt.client import *
import energenie


class EnergenieClient(Client):

    def __init__(self, client_id="", clean_session=True, userdata=None,
                 protocol=MQTTv311, transport="tcp"):
        Client.__init__(self, client_id, clean_session, userdata, protocol, transport)
        self.loop_fn = lambda *args: None

    def on_loop(self, fn):
        self.loop_fn = fn

    def loop_forever(self, timeout=1.0, max_packets=1, retry_first_connection=False):
        run = True

        while run:
            if self._thread_terminate is True:
                break

            if self._state == mqtt_cs_connect_async:
                try:
                    self.reconnect()
                except (socket.error, OSError, WebsocketConnectionError):
                    if not retry_first_connection:
                        raise
                    self._easy_log(MQTT_LOG_DEBUG, "Connection failed, retrying")
                    self._reconnect_wait()
            else:
                break

        while run:
            rc = MQTT_ERR_SUCCESS
            while rc == MQTT_ERR_SUCCESS:
                self.loop_fn()
                rc = self.loop(timeout, max_packets)

                if (self._thread_terminate is True
                        and self._current_out_packet is None
                        and len(self._out_packet) == 0
                        and len(self._out_messages) == 0):
                    rc = 1
                    run = False

            def should_exit():
                return self._state == mqtt_cs_disconnecting or run is False or self._thread_terminate is True

            if should_exit():
                run = False
            else:
                self._reconnect_wait()

                if should_exit():
                    run = False
                else:
                    try:
                        self.reconnect()
                    except (socket.error, WebsocketConnectionError):
                        pass

        return rc
