# Changelog

## 0.3.3

- Use the controller-required 100 ms minimum interval for legacy
  `ADV_NONCONN_IND` fallback advertisements.

## 0.3.2

- Pad the raw Linux HCI socket filter to its required 16-byte ABI size.

## 0.3.1

- Fall back automatically to direct legacy HCI commands when an HA OS adapter
  does not complete BlueZ's extended advertising management command.
- Preserve the proven non-connectable PDU and exact payload in the fallback
  transmitter.

## 0.3.0

- Request full hardware access so HA OS can expose the Bluetooth management
  socket required by the proven `btmgmt` transmitter.
- Make `btmgmt` the default backend. Protection mode must be disabled.

## 0.2.2

- Use a non-connectable BlueZ broadcast while retaining BlueZ-managed flags,
  matching the PDU type accepted by the floodlight.

## 0.2.1

- Let BlueZ generate the flags AD structure instead of supplying reserved AD
  type `0x01` through the generic `Data` property, which HA OS rejected while
  parsing the advertisement.

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
