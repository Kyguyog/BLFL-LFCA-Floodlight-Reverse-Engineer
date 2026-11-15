# BLFL-LFCA Floodlight Reverse Engineer

This light uses the proprietary BroadLink **FastCon/BRMesh** protocol over BLE
advertisements (not GATT and not standard Bluetooth Mesh). See
[`PROTOCOL.md`](PROTOCOL.md) for the packet format and current timer findings.

`brmesh.py` is an initial Linux controller for on/off, brightness, RGB, and
warm/cold white. It needs the four-byte mesh key and the ID assigned by BRmesh.

```bash
# First validate packet generation without touching Bluetooth or the light.
python3 brmesh.py --key b2fd16aa --id 3 --dry-run color ff8000 --brightness 60

# Then transmit. Brightness is a percentage from 0 through 100.
sudo python3 brmesh.py --key b2fd16aa --id 3 on --brightness 50
sudo python3 brmesh.py --key b2fd16aa --id 3 brightness 25
sudo python3 brmesh.py --key b2fd16aa --id 3 color 00a0ff --brightness 70
sudo python3 brmesh.py --key b2fd16aa --id 3 white --warm 255 --cold 0
sudo python3 brmesh.py --key b2fd16aa --id 3 off
```

The default `btmgmt` backend preserves the required flags-first byte order and
uses the experimentally confirmed 20-30 ms advertising interval. It needs
administrator access. The unprivileged D-Bus backend remains available for
patched BlueZ installations with `--backend bluez`.

```bash
sudo python3 brmesh.py --key b2fd16aa --id 3 color ff8000 --brightness 60
```

You can keep the key out of shell history with `BRMESH_KEY=b2fd16aa`. Do not
commit the real key. Run protocol tests with:

```bash
python3 -m unittest -v
```

Device-side scheduling is intentionally not implemented yet: the command is
not documented by the available protocol research, and it must be captured
and proven to run with the phone/controller absent.

## Home Assistant OS

[`brmesh_controller`](brmesh_controller/) is an installable Home Assistant OS
App (formerly called an add-on). It obtains broker credentials from Supervisor,
publishes MQTT-discovered RGB/brightness lights and a **Scan for lights**
button, and transmits through the Home Assistant machine's local Bluetooth
adapter. It scans at startup and can automatically bind lights found in their
power-on pairing window. See
[`brmesh_controller/DOCS.md`](brmesh_controller/DOCS.md) for setup and options.

After this repository is pushed to GitHub, add its URL to the Home Assistant
App store as a custom repository. For local testing, copy the
`brmesh_controller` directory into Home Assistant's `/local_apps` directory.
 
