"""Minimal BroadLink FastCon/BRMesh encoder and Linux advertiser."""

from __future__ import annotations

import os
import secrets
import socket
import struct
import subprocess
import threading
import time
import logging
from dataclasses import dataclass


DEFAULT_KEY = bytes.fromhex("5e367bc4")
FASTCON_ADDRESS = bytes((0xC1, 0xC2, 0xC3))
RF_PREAMBLE = bytes((0x71, 0x0F, 0x55))
RF_SKIP = 15

LOG = logging.getLogger(__name__)


def raw_advertisement(frame: bytes, flags: int = 0x02) -> bytes:
    if len(frame) != 24:
        raise ValueError("FastCon manufacturer frame must contain 24 bytes")
    return bytes((0x02, 0x01, flags, 0x1B, 0xFF, 0xF0, 0xFF)) + frame


def parse_key(value: str) -> bytes:
    cleaned = value.removeprefix("0x").replace(":", "").replace(" ", "")
    try:
        key = bytes.fromhex(cleaned)
    except ValueError as error:
        raise ValueError("mesh key must be eight hexadecimal digits") from error
    if len(key) != 4:
        raise ValueError("mesh key must be eight hexadecimal digits")
    return key


def _reverse8(value: int) -> int:
    return int(f"{value:08b}"[::-1], 2)


def _crc16(address: bytes, data: bytes) -> int:
    crc = 0xFFFF
    for byte in address + data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0x8408 if crc & 1 else 0)
    return crc ^ 0xFFFF


def _whiten(data: bytearray, seed: int = 0x25) -> None:
    f0 = 1
    f4, f8, fc, f10 = ((seed >> bit) & 1 for bit in (5, 4, 3, 2))
    f14, f18 = (seed >> 1) & 1, seed & 1
    for index, byte in enumerate(data):
        vc, v14, v18, v10 = fc, f14, f18, f10
        v8 = v14 ^ f8
        v4 = v10 ^ f4
        vx = v18 ^ vc
        v0 = vx ^ f0
        data[index] = (
            ((byte & 0x80) ^ ((v8 ^ v18) << 7))
            | ((byte & 0x40) ^ (v0 << 6))
            | ((byte & 0x20) ^ (v4 << 5))
            | ((byte & 0x10) ^ (v8 << 4))
            | ((byte & 0x08) ^ (vx << 3))
            | ((byte & 0x04) ^ (v10 << 2))
            | ((byte & 0x02) ^ (v14 << 1))
            | ((byte & 0x01) ^ v18)
        )
        f8, fc, f10 = v4, v8, v8 ^ vc
        f14, f18, f0, f4 = v0 ^ v10, v4 ^ v14, v8 ^ v18, v0


@dataclass
class FastConBuilder:
    sequence: int = 0

    def _next_sequence(self) -> int:
        self.sequence = (self.sequence + 1) & 0xFF
        if self.sequence == 0:
            self.sequence = 1
        return self.sequence

    def light(self, address: int, mesh_key: bytes, command: bytes) -> bytes:
        if not 1 <= address <= 0x0FFF:
            raise ValueError("light address must be in the range 1..4095")
        if len(mesh_key) != 4:
            raise ValueError("mesh key must contain four bytes")
        if not 1 <= len(command) <= 10:
            raise ValueError("light command must contain 1..10 bytes")

        content = (
            bytes((2 | ((len(command) + 1) << 4), address & 0xFF)) + command
        ).ljust(12, b"\0")
        body = bytearray(16)
        body[0] = (address >> 8) | (5 << 4) | 0x80
        body[1] = self._next_sequence()
        body[2] = mesh_key[3]
        body[4:] = content
        body[3] = (sum(body[:3]) + sum(content)) & 0xFF
        for index in range(4):
            body[index] ^= DEFAULT_KEY[index]
        for index in range(12):
            body[index + 4] ^= mesh_key[index & 3]

        rf = bytearray(RF_SKIP + 24)
        rf[RF_SKIP : RF_SKIP + 3] = RF_PREAMBLE
        rf[RF_SKIP + 3 : RF_SKIP + 6] = FASTCON_ADDRESS[::-1]
        rf[RF_SKIP + 6 : RF_SKIP + 22] = body
        for index in range(RF_SKIP, RF_SKIP + 6):
            rf[index] = _reverse8(rf[index])
        crc = _crc16(FASTCON_ADDRESS, body)
        rf[RF_SKIP + 22 : RF_SKIP + 24] = crc.to_bytes(2, "little")
        _whiten(rf)
        return bytes(rf[RF_SKIP:])


def _level(percent: float) -> int:
    if not 0 <= percent <= 100:
        raise ValueError("brightness must be between 0 and 100 percent")
    if percent == 0:
        return 0
    return max(1, round(percent * 127 / 100))


def command_power(on: bool, brightness: float = 100) -> bytes:
    level = _level(brightness)
    return bytes((0x80 | level,)) if on and level else b"\0"


def command_rgb(red: int, green: int, blue: int, brightness: float = 100) -> bytes:
    if any(not 0 <= value <= 255 for value in (red, green, blue)):
        raise ValueError("RGB channels must be in the range 0..255")
    total = red + green + blue
    if total == 0 or brightness == 0:
        return command_power(False)

    def normalise(value: int) -> int:
        return min(255, value * 255 // total)

    return bytes(
        (
            0x80 | _level(brightness),
            normalise(blue),
            normalise(red),
            normalise(green),
            0,
            0,
        )
    )


class BtmgmtAdvertiser:
    def __init__(self, duration: float, adapter: int, instance: int = 1) -> None:
        self.duration = duration
        self.adapter = adapter
        self.instance = instance
        self.hci_fallback = HciAdvertiser(duration, adapter)
        self.use_hci_fallback = False

    def _run(self, arguments: list[str], action: str, *, check: bool = True) -> None:
        try:
            result = subprocess.run(
                ["btmgmt", "--index", str(self.adapter), *arguments],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except subprocess.TimeoutExpired as error:
            if check:
                raise RuntimeError(
                    f"btmgmt timed out while trying to {action}; verify that "
                    f"hci{self.adapter} exists inside Home Assistant OS and is "
                    "a local adapter, not an ESPHome Bluetooth proxy"
                ) from error
            return
        if check and result.returncode:
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError(f"btmgmt could not {action}: {detail}")

    def send(self, manufacturer_data: bytes) -> None:
        if self.use_hci_fallback:
            self.hci_fallback.send(manufacturer_data)
            return

        payload = raw_advertisement(manufacturer_data)
        added = False
        configured = False
        try:
            try:
                self._run(
                    [
                        "add-ext-adv-params",
                        "-r",
                        "0x20",
                        "-x",
                        "0x30",
                        str(self.instance),
                    ],
                    "configure advertising",
                )
                configured = True
                self._run(
                    ["add-ext-adv-data", "-d", payload.hex(), str(self.instance)],
                    "set advertising data",
                )
            except RuntimeError as error:
                # Some HA OS controllers never complete the extended management
                # command. A raw legacy HCI command uses the same local radio but
                # avoids that controller/driver path and preserves the exact PDU.
                if configured:
                    self._run(
                        ["rm-adv", str(self.instance)],
                        "remove incomplete advertisement",
                        check=False,
                    )
                LOG.warning(
                    "%s; switching permanently to the legacy HCI transmitter",
                    error,
                )
                self.use_hci_fallback = True
                self.hci_fallback.send(manufacturer_data)
                return
            added = True
            time.sleep(self.duration)
        finally:
            if added:
                self._run(
                    ["rm-adv", str(self.instance)],
                    "remove advertisement",
                    check=False,
                )


class HciAdvertiser:
    """Transmit an exact legacy advertisement through raw HCI commands."""

    HCI_CHANNEL_RAW = 0
    HCI_COMMAND_PKT = 0x01
    HCI_EVENT_PKT = 0x04
    EVT_CMD_COMPLETE = 0x0E
    EVT_CMD_STATUS = 0x0F
    SOL_HCI = 0

    def __init__(self, duration: float, adapter: int) -> None:
        self.duration = duration
        self.adapter = adapter

    @staticmethod
    def _opcode(ogf: int, ocf: int) -> int:
        return (ogf << 10) | ocf

    def _open(self) -> socket.socket:
        try:
            radio = socket.socket(
                socket.AF_BLUETOOTH,
                socket.SOCK_RAW | getattr(socket, "SOCK_CLOEXEC", 0),
                socket.BTPROTO_HCI,
            )
            radio.bind((self.adapter, self.HCI_CHANNEL_RAW))
            radio.settimeout(3)
            return radio
        except OSError as error:
            raise RuntimeError(
                f"could not open raw HCI access to hci{self.adapter}: {error}; "
                "disable Protection mode and verify full hardware access"
            ) from error

    def _command(self, radio: socket.socket, opcode: int, parameters: bytes) -> None:
        # Receive command completion/status events for this opcode only.
        event_filter = struct.pack(
            "=IIIH", 1 << self.HCI_EVENT_PKT, 0xFFFFFFFF, 0xFFFFFFFF, opcode
        )
        radio.setsockopt(self.SOL_HCI, socket.HCI_FILTER, event_filter)
        radio.sendall(
            bytes((self.HCI_COMMAND_PKT,))
            + struct.pack("<HB", opcode, len(parameters))
            + parameters
        )

        while True:
            packet = radio.recv(260)
            if len(packet) < 3 or packet[0] != self.HCI_EVENT_PKT:
                continue
            event = packet[1]
            if event == self.EVT_CMD_COMPLETE and len(packet) >= 7:
                completed_opcode = struct.unpack_from("<H", packet, 4)[0]
                if completed_opcode != opcode:
                    continue
                status = packet[6]
                break
            if event == self.EVT_CMD_STATUS and len(packet) >= 7:
                completed_opcode = struct.unpack_from("<H", packet, 5)[0]
                if completed_opcode != opcode:
                    continue
                status = packet[3]
                break

        if status:
            raise RuntimeError(
                f"hci{self.adapter} rejected Bluetooth command 0x{opcode:04x} "
                f"with status 0x{status:02x}"
            )

    def send(self, manufacturer_data: bytes) -> None:
        payload = raw_advertisement(manufacturer_data)
        if len(payload) != 31:
            raise RuntimeError("legacy Bluetooth advertising payload must be 31 bytes")

        # Bluetooth Core legacy LE controller commands.
        set_parameters = self._opcode(0x08, 0x0006)
        set_data = self._opcode(0x08, 0x0008)
        set_enable = self._opcode(0x08, 0x000A)
        parameters = struct.pack(
            "<HHBBB6sBB",
            0x20,  # 20 ms minimum interval
            0x30,  # 30 ms maximum interval
            0x03,  # ADV_NONCONN_IND
            0x00,  # public own address
            0x00,
            bytes(6),
            0x07,  # advertise on channels 37, 38, and 39
            0x00,
        )

        radio = self._open()
        enabled = False
        try:
            # Clear an advertisement a previous interrupted run may have left on.
            try:
                self._command(radio, set_enable, b"\x00")
            except RuntimeError:
                pass
            self._command(radio, set_parameters, parameters)
            self._command(radio, set_data, bytes((len(payload),)) + payload)
            self._command(radio, set_enable, b"\x01")
            enabled = True
            time.sleep(self.duration)
        except socket.timeout as error:
            raise RuntimeError(
                f"hci{self.adapter} did not complete a raw Bluetooth command"
            ) from error
        finally:
            if enabled:
                try:
                    self._command(radio, set_enable, b"\x00")
                except (OSError, RuntimeError, socket.timeout):
                    pass
            radio.close()


class BluezAdvertiser:
    """Register a fast legacy advertisement with the host BlueZ service."""

    def __init__(self, duration: float, adapter: int) -> None:
        self.duration = duration
        self.adapter = adapter

    def send(self, manufacturer_data: bytes) -> None:
        try:
            import dbus
            import dbus.mainloop.glib
            import dbus.service
            from gi.repository import GLib
        except ImportError as error:
            raise RuntimeError("BlueZ backend dependencies are not installed") from error

        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        bus = dbus.SystemBus()
        manager_iface = "org.bluez.LEAdvertisingManager1"
        objects = dbus.Interface(
            bus.get_object("org.bluez", "/"), "org.freedesktop.DBus.ObjectManager"
        ).GetManagedObjects()
        preferred_path = f"/org/bluez/hci{self.adapter}"
        adapter_path = next(
            (
                path
                for path, interfaces in objects.items()
                if path == preferred_path and manager_iface in interfaces
            ),
            None,
        )
        if adapter_path is None:
            raise RuntimeError(
                f"host BlueZ does not expose hci{self.adapter} with LE advertising support"
            )

        advert_iface = "org.bluez.LEAdvertisement1"
        props_iface = "org.freedesktop.DBus.Properties"
        path = f"/org/brmesh/advertisement/{os.getpid()}_{secrets.randbelow(1_000_000)}"

        class Advertisement(dbus.service.Object):
            @dbus.service.method(props_iface, in_signature="s", out_signature="a{sv}")
            def GetAll(self, interface: str):
                if interface != advert_iface:
                    raise dbus.exceptions.DBusException(
                        "org.freedesktop.DBus.Error.InvalidArgs"
                    )
                return {
                    # BlueZ rejects a raw Data entry for the managed flags AD
                    # type on some HA OS releases. Broadcast + Discoverable
                    # makes BlueZ generate flags 0x02 while keeping the PDU
                    # non-connectable, matching the proven btmgmt packet.
                    "Type": dbus.String("broadcast"),
                    "Discoverable": dbus.Boolean(True),
                    "ManufacturerData": dbus.Dictionary(
                        {
                            dbus.UInt16(0xFFF0): dbus.Array(
                                manufacturer_data, signature="y"
                            )
                        },
                        signature="qv",
                    ),
                    "MinInterval": dbus.UInt32(20),
                    "MaxInterval": dbus.UInt32(30),
                }

            @dbus.service.method(advert_iface, in_signature="", out_signature="")
            def Release(self):
                return None

        advert = Advertisement(bus, path)
        manager = dbus.Interface(bus.get_object("org.bluez", adapter_path), manager_iface)
        loop = GLib.MainLoop()
        registration_error: list[Exception] = []
        registered = threading.Event()

        def registration_succeeded() -> None:
            registered.set()
            GLib.timeout_add(max(1, round(self.duration * 1000)), loop.quit)

        def registration_failed(error: Exception) -> None:
            registration_error.append(error)
            loop.quit()

        manager.RegisterAdvertisement(
            dbus.ObjectPath(path),
            {},
            reply_handler=registration_succeeded,
            error_handler=registration_failed,
        )
        loop.run()
        try:
            if registration_error:
                raise RuntimeError(
                    f"host BlueZ rejected the advertisement: {registration_error[0]}"
                )
            if not registered.is_set():
                raise RuntimeError("host BlueZ did not register the advertisement")
            manager.UnregisterAdvertisement(dbus.ObjectPath(path))
        finally:
            advert.remove_from_connection()
