"""Minimal BroadLink FastCon/BRMesh encoder and Linux advertiser."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass


DEFAULT_KEY = bytes.fromhex("5e367bc4")
FASTCON_ADDRESS = bytes((0xC1, 0xC2, 0xC3))
RF_PREAMBLE = bytes((0x71, 0x0F, 0x55))
RF_SKIP = 15


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
        payload = raw_advertisement(manufacturer_data)
        added = False
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
            self._run(
                ["add-ext-adv-data", "-d", payload.hex(), str(self.instance)],
                "set advertising data",
            )
            added = True
            time.sleep(self.duration)
        finally:
            if added:
                self._run(
                    ["rm-adv", str(self.instance)],
                    "remove advertisement",
                    check=False,
                )
