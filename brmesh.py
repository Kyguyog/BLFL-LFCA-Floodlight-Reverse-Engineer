#!/usr/bin/env python3
"""Control BRMesh / BroadLink FastCon lights with BLE advertisements.

The lights do not expose GATT characteristics.  They listen for encrypted
manufacturer data (company ID 0xFFF0) in legacy BLE advertisements.
"""

from __future__ import annotations

import argparse
import os
import secrets
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Sequence


DEFAULT_KEY = bytes.fromhex("5e367bc4")
FASTCON_ADDRESS = bytes((0xC1, 0xC2, 0xC3))
RF_PREAMBLE = bytes((0x71, 0x0F, 0x55))
RF_SKIP = 15
MANUFACTURER_ID = 0xFFF0


def raw_advertisement(frame: bytes, flags: int = 0x02) -> bytes:
    """Return the exact 31-byte BLE payload required by known BRMesh lamps."""
    if len(frame) != 24:
        raise ValueError("FastCon manufacturer frame must contain 24 bytes")
    if not 0 <= flags <= 0xFF:
        raise ValueError("BLE flags must fit in one byte")
    # Flags first, followed by a 27-byte manufacturer structure. Maintained
    # FastCon implementations use 0x02; older proofs of concept used 0x1A.
    return bytes((0x02, 0x01, flags, 0x1B, 0xFF, 0xF0, 0xFF)) + frame


def _reverse8(value: int) -> int:
    return int(f"{value:08b}"[::-1], 2)


def _crc16(address: bytes, data: bytes) -> int:
    """FastCon's reflected CRC-16/X-25."""
    crc = 0xFFFF
    for byte in address + data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0x8408 if crc & 1 else 0)
    return crc ^ 0xFFFF


def _whiten(data: bytearray, seed: int = 0x25) -> None:
    """Apply FastCon's 7-bit whitening LFSR (the operation is reversible)."""
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
    """Build FastCon frames while keeping the anti-duplicate sequence number."""

    sequence: int = 0

    def _next_sequence(self) -> int:
        self.sequence = (self.sequence + 1) & 0xFF
        if self.sequence == 0:
            self.sequence = 1
        return self.sequence

    def _generate(
        self,
        command_type: int,
        content: bytes,
        key: bytes | None,
        *,
        forward: bool,
        address_high: int = 0,
    ) -> bytes:
        if key is not None and len(key) != 4:
            raise ValueError("mesh key must contain exactly four bytes")
        if len(content) > 12:
            raise ValueError("FastCon content cannot exceed 12 bytes")

        content = content.ljust(12, b"\0")
        body = bytearray(16)
        body[0] = (
            (address_high & 0x0F)
            | ((command_type & 0x07) << 4)
            | (0x80 if forward else 0)
        )
        body[1] = self._next_sequence()
        body[2] = key[3] if key is not None else 0xFF
        body[4:] = content
        body[3] = (sum(body[:3]) + sum(content)) & 0xFF
        for index in range(4):
            body[index] ^= DEFAULT_KEY[index]
        encryption_key = key if key is not None else DEFAULT_KEY
        for index in range(12):
            body[index + 4] ^= encryption_key[index & 3]

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

    def scan(self) -> bytes:
        """Ask a lamp in its discovery window to announce its device record."""
        return self._generate(0, bytes(12), None, forward=False)

    def light(self, address: int, mesh_key: bytes, command: bytes) -> bytes:
        if not 1 <= address <= 0x0FFF:
            raise ValueError("light ID/address must be in the range 1..4095")
        if not 1 <= len(command) <= 10:
            raise ValueError("light command must contain 1..10 bytes")
        content = bytes((2 | ((len(command) + 1) << 4), address & 0xFF)) + command
        return self._generate(
            5, content, mesh_key, forward=True, address_high=address >> 8
        )


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

    # FastCon order is brightness, blue, red, green, warm, cold.
    return bytes(
        (0x80 | _level(brightness), normalise(blue), normalise(red), normalise(green), 0, 0)
    )


def command_white(warm: int, cold: int, brightness: float = 100) -> bytes:
    if any(not 0 <= value <= 255 for value in (warm, cold)):
        raise ValueError("warm/cold channels must be in the range 0..255")
    if brightness == 0:
        return command_power(False)
    return bytes((0x80 | _level(brightness), 0, 0, 0, warm, cold))


def _parse_key(value: str) -> bytes:
    cleaned = value.removeprefix("0x").replace(":", "").replace(" ", "")
    try:
        key = bytes.fromhex(cleaned)
    except ValueError as error:
        raise argparse.ArgumentTypeError("mesh key must be eight hex digits") from error
    if len(key) != 4:
        raise argparse.ArgumentTypeError("mesh key must be exactly eight hex digits")
    return key


class BluezAdvertiser:
    """Temporarily register a legacy manufacturer advertisement with BlueZ."""

    def __init__(self, duration: float = 0.25) -> None:
        self.duration = duration

    def send(self, manufacturer_data: bytes) -> None:
        try:
            import dbus
            import dbus.mainloop.glib
            import dbus.service
            from gi.repository import GLib
        except ImportError as error:
            raise RuntimeError(
                "BlueZ backend needs Debian packages python3-dbus and python3-gi"
            ) from error

        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        bus = dbus.SystemBus()
        manager_iface = "org.bluez.LEAdvertisingManager1"
        objects = dbus.Interface(
            bus.get_object("org.bluez", "/"), "org.freedesktop.DBus.ObjectManager"
        ).GetManagedObjects()
        adapter_path = next(
            (path for path, interfaces in objects.items() if manager_iface in interfaces),
            None,
        )
        if adapter_path is None:
            raise RuntimeError("no powered BlueZ adapter with LE advertising support found")

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
                    "Type": dbus.String("peripheral"),
                    "Discoverable": dbus.Boolean(True),
                    "ManufacturerData": dbus.Dictionary(
                        {
                            dbus.UInt16(MANUFACTURER_ID): dbus.Array(
                                manufacturer_data, signature="y"
                            )
                        },
                        signature="qv",
                    ),
                }

            @dbus.service.method(advert_iface, in_signature="", out_signature="")
            def Release(self):
                return None

        advert = Advertisement(bus, path)
        manager = dbus.Interface(bus.get_object("org.bluez", adapter_path), manager_iface)
        loop = GLib.MainLoop()
        registration_error: list[Exception] = []

        def registered() -> None:
            GLib.timeout_add(max(1, round(self.duration * 1000)), loop.quit)

        def failed(error: Exception) -> None:
            registration_error.append(error)
            loop.quit()

        manager.RegisterAdvertisement(
            dbus.ObjectPath(path), {}, reply_handler=registered, error_handler=failed
        )
        loop.run()
        try:
            if registration_error:
                raise RuntimeError(f"BlueZ rejected advertisement: {registration_error[0]}")
            manager.UnregisterAdvertisement(dbus.ObjectPath(path))
        finally:
            advert.remove_from_connection()


class BtmgmtAdvertiser:
    """Advertise an exact payload through the privileged BlueZ management API."""

    def __init__(
        self,
        duration: float = 0.25,
        adapter: int = 0,
        instance: int = 1,
        flags: int = 0x02,
    ) -> None:
        self.duration = duration
        self.adapter = adapter
        self.instance = instance
        self.flags = flags

    def send(self, manufacturer_data: bytes) -> None:
        payload = raw_advertisement(manufacturer_data, self.flags)
        base = ["btmgmt", "--index", str(self.adapter)]
        added = False

        def run_btmgmt(arguments: list[str], action: str) -> None:
            try:
                result = subprocess.run(
                    [*base, *arguments],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
            except FileNotFoundError as error:
                raise RuntimeError("btmgmt is not installed") from error
            except subprocess.TimeoutExpired as error:
                raise RuntimeError(f"btmgmt timed out while {action}") from error
            if result.returncode:
                detail = (result.stderr or result.stdout).strip()
                if "permission" in detail.lower() or "not permitted" in detail.lower():
                    detail += "; run the complete brmesh.py command with sudo"
                raise RuntimeError(f"btmgmt could not {action}: {detail}")

        try:
            # FastCon reference transmitters advertise every 20-30 ms. The
            # legacy add-adv API otherwise selected 1280 ms on this adapter.
            # Omitting -P deliberately selects a legacy advertising PDU.
            run_btmgmt(
                [
                    "add-ext-adv-params",
                    "-r",
                    "0x20",
                    "-x",
                    "0x30",
                    str(self.instance),
                ],
                "configure the advertising interval",
            )
            run_btmgmt(
                ["add-ext-adv-data", "-d", payload.hex(), str(self.instance)],
                "add the advertising data",
            )
            added = True
            time.sleep(self.duration)
        finally:
            if added:
                subprocess.run(
                    [*base, "rm-adv", str(self.instance)],
                    check=False,
                    capture_output=True,
                    timeout=5,
                )


def _rgb(value: str) -> tuple[int, int, int]:
    cleaned = value.removeprefix("#")
    if len(cleaned) != 6:
        raise argparse.ArgumentTypeError("color must be RRGGBB, for example ff8000")
    try:
        raw = bytes.fromhex(cleaned)
    except ValueError as error:
        raise argparse.ArgumentTypeError("color must contain hexadecimal digits") from error
    return raw[0], raw[1], raw[2]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key", type=_parse_key, default=None, help="4-byte mesh key in hex")
    parser.add_argument("--id", type=int, required=True, help="BRmesh light ID/address")
    parser.add_argument("--duration", type=float, default=0.25, help="advertisement seconds")
    parser.add_argument(
        "--backend",
        choices=("bluez", "btmgmt"),
        default="btmgmt",
        help="radio backend (default: proven btmgmt backend, which needs sudo)",
    )
    parser.add_argument("--adapter-index", type=int, default=0, help="btmgmt HCI index")
    parser.add_argument(
        "--ble-flags",
        type=lambda value: int(value, 0),
        default=0x02,
        help="raw backend flags byte (default 0x02; older implementations used 0x1a)",
    )
    parser.add_argument("--dry-run", action="store_true", help="print packet; do not use radio")
    sub = parser.add_subparsers(dest="action", required=True)
    on = sub.add_parser("on")
    on.add_argument("--brightness", type=float, default=100, help="percent, 0..100")
    sub.add_parser("off")
    brightness = sub.add_parser("brightness")
    brightness.add_argument("percent", type=float, help="0..100")
    color = sub.add_parser("color")
    color.add_argument("rgb", type=_rgb, help="RRGGBB or #RRGGBB")
    color.add_argument("--brightness", type=float, default=100, help="percent, 0..100")
    white = sub.add_parser("white")
    white.add_argument("--warm", type=int, default=127)
    white.add_argument("--cold", type=int, default=127)
    white.add_argument("--brightness", type=float, default=100, help="percent, 0..100")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    key = args.key
    if key is None and os.environ.get("BRMESH_KEY"):
        key = _parse_key(os.environ["BRMESH_KEY"])
    if key is None:
        parser.error("provide --key or set BRMESH_KEY")

    try:
        if args.action == "off":
            command = command_power(False)
        elif args.action == "on":
            command = command_power(True, args.brightness)
        elif args.action == "brightness":
            command = command_power(args.percent > 0, args.percent)
        elif args.action == "color":
            command = command_rgb(*args.rgb, args.brightness)
        else:
            command = command_white(args.warm, args.cold, args.brightness)
        frame = FastConBuilder(secrets.randbelow(254) + 1).light(args.id, key, command)
        print(f"manufacturer=0x{MANUFACTURER_ID:04x} frame={frame.hex()}")
        if not args.dry_run:
            if args.backend == "btmgmt":
                BtmgmtAdvertiser(
                    args.duration, args.adapter_index, flags=args.ble_flags
                ).send(frame)
            else:
                BluezAdvertiser(args.duration).send(frame)
    except (RuntimeError, ValueError) as error:
        parser.exit(1, f"error: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
