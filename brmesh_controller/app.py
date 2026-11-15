"""MQTT discovery, control, scanning, and pairing for BRMesh lights."""

from __future__ import annotations

import json
import logging
import os
import queue
import secrets
import signal
import ssl
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import paho.mqtt.client as mqtt

from brmesh_protocol import (
    BluezAdvertiser,
    BluezScanner,
    BtmgmtAdvertiser,
    FastConBuilder,
    LightCommandState,
    WhiteCommandState,
    is_light_type,
    parse_discovery,
    parse_heartbeat,
    parse_key,
)


VERSION = "0.5.3"
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
LOG = logging.getLogger("brmesh-controller")


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise RuntimeError(f"missing environment variable {name}")
    return value


def env_bool(name: str, default: bool) -> bool:
    return env(name, str(default)).lower() in {"1", "true", "yes", "on"}


@dataclass
class ManagedLight:
    uid: str
    name: str
    address: int
    device_type: int = 0
    device_id: str = ""
    device_key: str = ""
    state: LightCommandState = field(default_factory=LightCommandState, repr=False)
    white_state: WhiteCommandState = field(default_factory=WhiteCommandState, repr=False)

    def saved(self) -> dict:
        return {
            "uid": self.uid,
            "name": self.name,
            "address": self.address,
            "device_type": self.device_type,
            "device_id": self.device_id,
            "device_key": self.device_key,
        }


class Bridge:
    def __init__(self) -> None:
        self.key = parse_key(env("BRMESH_KEY"))
        self.adapter = int(env("BRMESH_ADAPTER_INDEX", "0"))
        self.duration = float(env("BRMESH_ADVERTISE_SECONDS", "1.0"))
        self.radio_backend = env("BRMESH_RADIO_BACKEND", "btmgmt")
        self.auto_pair = env_bool("BRMESH_AUTO_PAIR", True)
        self.keep_manual_light = env_bool("BRMESH_KEEP_MANUAL_LIGHT", True)
        self.manual_address = int(env("BRMESH_ADDRESS"))
        self.prefix = env("BRMESH_MQTT_PREFIX", "brmesh").strip("/")
        self.availability_topic = f"{self.prefix}/controller/availability"
        self.scan_topic = f"{self.prefix}/controller/scan"
        self.store_path = Path(env("BRMESH_STORE_PATH", "/data/devices.json"))
        self.lock = threading.RLock()
        self.scanner_ready = threading.Event()
        self.builder = FastConBuilder(secrets.randbelow(254) + 1)
        self.jobs: queue.Queue[tuple] = queue.Queue(maxsize=32)
        self.pending_binds: dict[int, str] = {}
        self.last_bind: dict[str, float] = {}
        self.unknown_devices: set[str] = set()
        self.lights = self.load_lights()

        if self.radio_backend == "bluez":
            self.advertiser = BluezAdvertiser(self.duration, self.adapter)
        elif self.radio_backend == "btmgmt":
            self.advertiser = BtmgmtAdvertiser(self.duration, self.adapter)
        else:
            raise RuntimeError(f"unknown radio backend {self.radio_backend!r}")

        self.scanner = BluezScanner(
            self.adapter, self.handle_advertisement, self.scanner_ready.set
        )
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id="brmesh-controller",
        )
        username = env("MQTT_USERNAME", "")
        if username:
            self.client.username_pw_set(username, env("MQTT_PASSWORD", ""))
        if env_bool("MQTT_SSL", False):
            self.client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
        self.client.will_set(self.availability_topic, "offline", retain=True)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

    def load_lights(self) -> list[ManagedLight]:
        lights: list[ManagedLight] = []
        try:
            raw = json.loads(self.store_path.read_text())
            for item in raw.get("lights", []):
                lights.append(
                    ManagedLight(
                        uid=str(item["uid"]),
                        name=str(item["name"]),
                        address=int(item["address"]),
                        device_type=int(item.get("device_type", 0)),
                        device_id=str(item.get("device_id", "")),
                        device_key=str(item.get("device_key", "")),
                    )
                )
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError) as error:
            LOG.error("Could not load %s: %s", self.store_path, error)

        manual_uid = f"light_{self.manual_address}"
        if not self.keep_manual_light:
            filtered = [light for light in lights if light.uid != manual_uid]
            if len(filtered) != len(lights):
                LOG.info("Removed manually configured light at address %d", self.manual_address)
                lights = filtered
                self.save_lights(lights)

        if not lights and self.keep_manual_light:
            lights.append(
                ManagedLight(
                    uid=manual_uid,
                    name=env("BRMESH_DEVICE_NAME"),
                    address=self.manual_address,
                )
            )
            self.save_lights(lights)
        return lights

    def save_lights(self, lights: list[ManagedLight] | None = None) -> None:
        if lights is None:
            lights = self.lights
        payload = json.dumps({"version": 1, "lights": [light.saved() for light in lights]})
        try:
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.store_path.with_suffix(".tmp")
            temporary.write_text(payload)
            os.replace(temporary, self.store_path)
        except OSError as error:
            LOG.error("Could not save paired lights to %s: %s", self.store_path, error)

    def light_base(self, light: ManagedLight) -> str:
        if light.uid == f"light_{light.address}":
            return f"{self.prefix}/{light.uid}"
        return f"{self.prefix}/light/{light.uid}"

    def light_discovery_topic(self, light: ManagedLight) -> str:
        return f"homeassistant/light/brmesh/{light.uid}/config"

    def light_discovery_payload(self, light: ManagedLight) -> str:
        model = (
            f"FastCon type {light.device_type}"
            if light.device_type
            else "BLFL-LFCA Floodlight"
        )
        device_identifier = (
            f"brmesh_{light.address}"
            if light.uid == f"light_{light.address}"
            else f"brmesh_{light.uid}"
        )
        return json.dumps(
            {
                "name": light.name,
                "unique_id": f"brmesh_{light.uid}",
                "command_topic": f"{self.light_base(light)}/set",
                "schema": "json",
                "brightness": True,
                "supported_color_modes": ["rgb"],
                "optimistic": True,
                "availability_topic": self.availability_topic,
                "device": {
                    "identifiers": [device_identifier],
                    "name": light.name,
                    "manufacturer": "BroadLink FastCon / BRMesh",
                    "model": model,
                    "via_device": "brmesh_controller",
                },
                "origin": {
                    "name": "BRMesh Floodlight Controller",
                    "sw_version": VERSION,
                    "support_url": "https://github.com/Kyguyog/BLFL-LFCA-Floodlight-Reverse-Engineer",
                },
            },
            separators=(",", ":"),
        )

    def scan_discovery_payload(self) -> str:
        return json.dumps(
            {
                "name": "Scan for lights",
                "unique_id": "brmesh_controller_scan",
                "command_topic": self.scan_topic,
                "payload_press": "SCAN",
                "availability_topic": self.availability_topic,
                "icon": "mdi:magnify-scan",
                "device": {
                    "identifiers": ["brmesh_controller"],
                    "name": "BRMesh Controller",
                    "manufacturer": "BroadLink FastCon / BRMesh",
                    "model": "Home Assistant OS controller",
                    "sw_version": VERSION,
                },
                "origin": {
                    "name": "BRMesh Floodlight Controller",
                    "sw_version": VERSION,
                    "support_url": "https://github.com/Kyguyog/BLFL-LFCA-Floodlight-Reverse-Engineer",
                },
            },
            separators=(",", ":"),
        )

    def white_discovery_topic(self, light: ManagedLight) -> str:
        return f"homeassistant/light/brmesh/{light.uid}_white/config"

    def white_command_topic(self, light: ManagedLight) -> str:
        return f"{self.light_base(light)}/white/set"

    def white_discovery_payload(self, light: ManagedLight) -> str:
        payload = json.loads(self.light_discovery_payload(light))
        payload.update(
            {
                "name": "White LEDs",
                "unique_id": f"brmesh_{light.uid}_white",
                "command_topic": self.white_command_topic(light),
                "supported_color_modes": ["brightness"],
            }
        )
        return json.dumps(payload, separators=(",", ":"))

    def publish_light(self, light: ManagedLight) -> None:
        self.client.subscribe(f"{self.light_base(light)}/set")
        self.client.subscribe(self.white_command_topic(light))
        self.client.publish(
            self.light_discovery_topic(light),
            self.light_discovery_payload(light),
            retain=True,
        )
        self.client.publish(
            self.white_discovery_topic(light),
            self.white_discovery_payload(light),
            retain=True,
        )

    def on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code != 0:
            LOG.error("MQTT connection failed: %s", reason_code)
            return
        LOG.info("Connected to MQTT; publishing Home Assistant discovery")
        client.subscribe(self.scan_topic)
        client.publish(
            "homeassistant/button/brmesh/scan/config",
            self.scan_discovery_payload(),
            retain=True,
        )
        if not self.keep_manual_light:
            client.publish(
                f"homeassistant/light/brmesh/light_{self.manual_address}/config",
                "",
                retain=True,
            )
        with self.lock:
            for light in self.lights:
                self.publish_light(light)
        client.publish(self.availability_topic, "online", retain=True)
        threading.Thread(
            target=self.start_scan,
            kwargs={"source": "startup"},
            name="startup-scan",
            daemon=True,
        ).start()

    def on_message(self, client, userdata, message) -> None:
        if message.topic == self.scan_topic:
            self.start_scan(source="Home Assistant button")
            return
        try:
            payload = json.loads(message.payload)
            with self.lock:
                light = next(
                    item
                    for item in self.lights
                    if message.topic
                    in {
                        f"{self.light_base(item)}/set",
                        self.white_command_topic(item),
                    }
                )
                if message.topic == self.white_command_topic(light):
                    command = light.white_state.command_from_json(payload)
                else:
                    command = light.state.command_from_json(payload)
                self.enqueue(("light", light.address, command))
        except StopIteration:
            LOG.warning("Ignoring command for unknown MQTT topic %s", message.topic)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            LOG.error("Ignoring invalid light command: %s", error)

    def enqueue(self, job: tuple) -> None:
        try:
            self.jobs.put_nowait(job)
        except queue.Full:
            LOG.error("Bluetooth command queue is full; dropping %s", job[0])

    def start_scan(self, source: str) -> None:
        if not self.scanner_ready.wait(timeout=5):
            LOG.warning("BLE receiver is not ready; transmitting scan anyway")
        LOG.info("Scanning for FastCon lights in pairing mode (%s)", source)
        self.enqueue(("scan",))

    def next_address(self, preferred: int) -> int | None:
        used = {light.address for light in self.lights}
        if 1 <= preferred <= 255 and preferred not in used:
            return preferred
        return next((address for address in range(1, 256) if address not in used), None)

    def queue_bind(self, light: ManagedLight) -> bool:
        if not light.device_id or not light.device_key:
            return False
        now = time.monotonic()
        if now - self.last_bind.get(light.uid, 0) < 3:
            return False
        self.last_bind[light.uid] = now
        self.pending_binds[light.address] = light.uid
        self.enqueue(
            (
                "bind",
                bytes.fromhex(light.device_id),
                light.address,
                bytes.fromhex(light.device_key),
            )
        )
        return True

    def handle_advertisement(self, payload: bytes, rssi: int) -> None:
        heartbeat = parse_heartbeat(payload, self.key)
        if heartbeat is not None:
            with self.lock:
                uid = self.pending_binds.pop(heartbeat.address, None)
                if uid is not None:
                    light = next((item for item in self.lights if item.uid == uid), None)
                    if light is not None:
                        LOG.info(
                            "%s joined the mesh at address %d",
                            light.name,
                            heartbeat.address,
                        )
            return

        discovery = parse_discovery(payload)
        if discovery is None:
            return
        did = discovery.device_id.hex()
        if not is_light_type(discovery.device_type):
            if did not in self.unknown_devices:
                self.unknown_devices.add(did)
                LOG.info(
                    "Ignoring non-light FastCon device %s (type %d)",
                    did,
                    discovery.device_type,
                )
            return

        with self.lock:
            light = next((item for item in self.lights if item.device_id == did), None)
            if light is None:
                light = next(
                    (
                        item
                        for item in self.lights
                        if not item.device_id
                        and discovery.reported_address == item.address
                    ),
                    None,
                )
            if light is None:
                address = self.next_address(discovery.reported_address)
                if address is None:
                    LOG.error("No unused FastCon mesh address is available for %s", did)
                    return
                light = ManagedLight(
                    uid=did,
                    name=f"Light {discovery.device_id[-2:].hex().upper()}",
                    address=address,
                )
                self.lights.append(light)
                LOG.info(
                    "Discovered new FastCon light %s (type %d, address %d, %d dBm)",
                    did,
                    discovery.device_type,
                    discovery.reported_address,
                    rssi,
                )

            changed = (
                light.device_id != did
                or light.device_key != discovery.device_key.hex()
                or light.device_type != discovery.device_type
            )
            light.device_id = did
            light.device_key = discovery.device_key.hex()
            light.device_type = discovery.device_type
            if changed:
                self.save_lights()
                self.publish_light(light)

            if self.auto_pair:
                if self.queue_bind(light):
                    LOG.info("Pairing %s to mesh address %d", light.name, light.address)
            else:
                LOG.info(
                    "Found %s at %d dBm; automatic pairing is disabled",
                    light.name,
                    rssi,
                )

    def worker(self) -> None:
        while True:
            job = self.jobs.get()
            try:
                if job[0] == "scan":
                    frame = self.builder.scan()
                    description = "FastCon discovery scan"
                elif job[0] == "bind":
                    _, device_id, address, device_key = job
                    frame = self.builder.bind(device_id, address, self.key, device_key)
                    description = f"pairing command for address {address}"
                else:
                    _, address, command = job
                    frame = self.builder.light(address, self.key, command)
                    description = f"light command to address {address}"
                self.advertiser.send(frame)
                LOG.info("Sent %s", description)
            except Exception:
                LOG.exception("Bluetooth transmission failed")
            finally:
                self.jobs.task_done()

    def scanner_worker(self) -> None:
        try:
            self.scanner.run()
        except Exception:
            LOG.exception("Bluetooth discovery receiver failed")

    def run(self) -> None:
        threading.Thread(
            target=self.scanner_worker, name="bluetooth-scanner", daemon=True
        ).start()
        threading.Thread(
            target=self.worker, name="bluetooth-worker", daemon=True
        ).start()
        host = env("MQTT_HOST")
        port = int(env("MQTT_PORT", "1883"))
        LOG.info(
            "Loaded %d light(s); connecting to MQTT at %s:%d",
            len(self.lights),
            host,
            port,
        )
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
