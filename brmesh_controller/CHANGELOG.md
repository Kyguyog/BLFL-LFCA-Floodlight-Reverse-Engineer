# Changelog

## 0.2.0

- Add a host BlueZ D-Bus advertising backend for Home Assistant OS.
- Set the required advertisement flags and 20-30 ms interval through BlueZ.
- Retain `btmgmt` as an optional backend for systems exposing raw management.

## 0.1.1

- Do not remove a nonexistent advertising instance before transmission. Some
  Home Assistant OS Bluetooth stacks never answer that management command.
- Report Bluetooth management timeouts with an adapter-specific diagnostic.

## 0.1.0

- Add Home Assistant MQTT discovery for a single BRMesh floodlight.
- Support on/off, brightness, and RGB commands.
- Transmit the physically verified 20-30 ms BLE advertisement format with
  `btmgmt`.
