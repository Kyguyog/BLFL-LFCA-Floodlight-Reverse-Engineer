"""MQTT discovery bridge for a BRMesh floodlight."""

from __future__ import annotations

import json
import logging
import os
import queue
import secrets
import signal
import ssl
import threading

import paho.mqtt.client as mqtt

from brmesh_protocol import (
    BluezAdvertiser,
    BtmgmtAdvertiser,
    FastConBuilder,
    command_power,
    command_rgb,
    parse_key,
)


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
LOG = logging.getLogger("brmesh-controller")


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise RuntimeError(f"missing environment variable {name}")
    return value


class Bridge:
    def __init__(self) -> None:
        self.key = parse_key(env("BRMESH_KEY"))
        self.address = int(env("BRMESH_ADDRESS"))
        self.name = env("BRMESH_DEVICE_NAME")
        self.adapter = int(env("BRMESH_ADAPTER_INDEX", "0"))
        self.duration = float(env("BRMESH_ADVERTISE_SECONDS", "1.0"))
        self.radio_backend = env("BRMESH_RADIO_BACKEND", "bluez")
        prefix = env("BRMESH_MQTT_PREFIX", "brmesh").strip("/")
        self.object_id = f"light_{self.address}"
        self.base_topic = f"{prefix}/{self.object_id}"
        self.command_topic = f"{self.base_topic}/set"
        self.availability_topic = f"{self.base_topic}/availability"
        self.discovery_topic = f"homeassistant/light/brmesh/{self.object_id}/config"
        self.commands: queue.Queue[bytes] = queue.Queue(maxsize=1)
        self.builder = FastConBuilder(secrets.randbelow(254) + 1)
        if self.radio_backend == "bluez":
            self.advertiser = BluezAdvertiser(self.duration, self.adapter)
        elif self.radio_backend == "btmgmt":
            self.advertiser = BtmgmtAdvertiser(self.duration, self.adapter)
        else:
            raise RuntimeError(f"unknown radio backend {self.radio_backend!r}")
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"brmesh-controller-{self.address}",
        )
        username = env("MQTT_USERNAME", "")
        if username:
            self.client.username_pw_set(username, env("MQTT_PASSWORD", ""))
        if env("MQTT_SSL", "false").lower() in {"1", "true", "yes", "on"}:
            self.client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
        self.client.will_set(self.availability_topic, "offline", retain=True)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

    def discovery_payload(self) -> str:
        return json.dumps(
            {
                "name": self.name,
                "unique_id": f"brmesh_light_{self.address}",
                "command_topic": self.command_topic,
                "schema": "json",
                "brightness": True,
                "supported_color_modes": ["rgb"],
                "optimistic": True,
                "availability_topic": self.availability_topic,
                "device": {
                    "identifiers": [f"brmesh_{self.address}"],
                    "name": self.name,
                    "manufacturer": "BroadLink FastCon / BRMesh",
                    "model": "BLFL-LFCA Floodlight",
                },
                "origin": {
                    "name": "BRMesh Floodlight Controller",
                    "sw_version": "0.2.2",
                    "support_url": "https://github.com/Kyguyog/BLFL-LFCA-Floodlight-Reverse-Engineer",
                },
            },
            separators=(",", ":"),
        )

    def on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code != 0:
            LOG.error("MQTT connection failed: %s", reason_code)
            return
        LOG.info("Connected to MQTT; publishing Home Assistant discovery")
        client.subscribe(self.command_topic)
        client.publish(self.discovery_topic, self.discovery_payload(), retain=True)
        client.publish(self.availability_topic, "online", retain=True)

    def on_message(self, client, userdata, message) -> None:
        try:
            payload = json.loads(message.payload)
            command = self.command_from_json(payload)
            self.enqueue(command)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            LOG.error("Ignoring invalid light command: %s", error)

    @staticmethod
    def command_from_json(payload: dict) -> bytes:
        if str(payload.get("state", "ON")).upper() == "OFF":
            return command_power(False)

        brightness = max(0, min(255, int(payload.get("brightness", 255))))
        percent = brightness * 100 / 255
        color = payload.get("color")
        if color is not None:
            return command_rgb(
                int(color["r"]), int(color["g"]), int(color["b"]), percent
            )
        return command_power(brightness > 0, percent)

    def enqueue(self, command: bytes) -> None:
        try:
            self.commands.put_nowait(command)
        except queue.Full:
            try:
                self.commands.get_nowait()
                self.commands.task_done()
            except queue.Empty:
                pass
            self.commands.put_nowait(command)

    def worker(self) -> None:
        while True:
            command = self.commands.get()
            try:
                frame = self.builder.light(self.address, self.key, command)
                self.advertiser.send(frame)
                LOG.info("Sent command to BRMesh address %d", self.address)
            except Exception:
                LOG.exception("Bluetooth transmission failed")
            finally:
                self.commands.task_done()

    def run(self) -> None:
        thread = threading.Thread(target=self.worker, name="bluetooth-worker", daemon=True)
        thread.start()
        host = env("MQTT_HOST")
        port = int(env("MQTT_PORT", "1883"))
        LOG.info("Connecting to MQTT at %s:%d", host, port)
        self.client.connect(host, port, keepalive=60)
        self.client.loop_forever(retry_first_connection=True)


def main() -> None:
    bridge = Bridge()

    def stop(signum, frame) -> None:
        bridge.client.publish(bridge.availability_topic, "offline", retain=True)
        bridge.client.disconnect()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    bridge.run()


if __name__ == "__main__":
    main()
