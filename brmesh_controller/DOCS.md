# BRMesh Floodlight Controller

This app controls and pairs BLFL-LFCA/BroadLink FastCon lights through the
Bluetooth adapter attached to the Home Assistant OS machine. It creates normal
Home Assistant light entities through MQTT discovery.

Each discovered floodlight has an RGB entity plus a **White LEDs** entity. The
white entity switches RGBW models to their dedicated fixed-white LEDs and
controls their brightness. Both entities include power controls, though
switching either entity off turns off the whole physical floodlight.

## Before installing

Install and start the official **Mosquitto broker** app, then add the **MQTT**
integration if Home Assistant has not already discovered it. No Home Assistant
access token or MQTT credentials are needed; Supervisor supplies a dedicated
MQTT account to this app.

The Home Assistant machine must have a local Bluetooth adapter that supports
BLE advertising. An ESPHome Bluetooth proxy cannot transmit these proprietary
BRMesh control advertisements for this app.

## Configuration

- `mesh_key`: Eight hexadecimal digits from BRmesh. This is displayed as a
  password and is not built into the image.
- `address`: The numeric BRmesh mesh address, from 1 to 4095.
- `device_name`: The name shown in Home Assistant.
- `adapter_index`: Linux Bluetooth adapter number; `0` means `hci0`.
- `radio_backend`: `bluez` uses HA OS's host Bluetooth service and is the
  restricted fallback. `btmgmt` is the default and the only backend known to
  produce the exact non-connectable packet accepted by this floodlight. If the
  adapter does not complete extended management commands, `btmgmt` automatically
  switches to a raw legacy HCI transmitter at the controller-compatible 100 ms
  interval.
- `keep_manual_light`: Keep the compatibility entity created from `address` and
  `device_name`. Turn this off to remove it and use only automatically paired
  lights. The address and name fields remain visible but are ignored.
- `auto_pair`: When enabled, a discovered light is assigned an unused mesh
  address and bound to the configured mesh key automatically.
- `advertise_seconds`: How long each command is repeated. `1.0` is the proven
  reliable value; lower values respond faster but may be less reliable.
- `mqtt_topic_prefix`: Root topic used by the bridge. Usually leave this as
  `brmesh`.

Save the options, start the app, and inspect its Log tab. The new light should
appear under **Settings > Devices & services > MQTT**.

## Discovering and pairing lights

The app scans once whenever it starts and creates a **Scan for lights** button
under the BRMesh Controller MQTT device. A light only answers during its short
power-on pairing window:

1. Switch the light off at the wall for at least five seconds.
2. Switch it on again.
3. Press **Scan for lights** in Home Assistant.

With `auto_pair` enabled, the app saves the device ID, device type, device key,
and assigned mesh address under `/data/devices.json`, sends the FastCon bind
command, and publishes the new light entity. A heartbeat from the assigned
address is logged as pairing confirmation. The saved list survives app updates
and restarts.

## Protection mode

This app requires **Protection mode to be disabled** on its Info page. The
FastCon lamp requires an exact raw Bluetooth management advertisement that the
host BlueZ D-Bus API cannot reproduce. The app therefore requests full hardware
access and the `NET_ADMIN`/`NET_RAW` capabilities. Only install it from source
you trust.

## Limitations

The lamp does not report its state, so the entity is optimistic: Home Assistant
shows the last command it sent. On/off, brightness, and RGB are supported.
Device-side clock/timer/schedule commands are not included because their packet
format has not yet been captured and verified to execute without a phone or
Home Assistant present.
