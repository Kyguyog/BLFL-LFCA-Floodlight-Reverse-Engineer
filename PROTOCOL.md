# BLFL-LFCA protocol notes

## Identification

BLFL-LFCA is sold under several names, including MELPO, ILC, ChangM, and
HEKEE. The manuals point to the **BRmesh** mobile app. Its radio protocol is
BroadLink **FastCon/BRMesh**. Despite the marketing name, this is not the
Bluetooth SIG Mesh Profile and it is not a GATT service.

The controller transmits legacy BLE advertisements. Their relevant layout is:

```text
02 01 02                 BLE flags (must be first for known lights)
1b ff f0 ff <24 bytes>   manufacturer AD, company ID 0xFFF0
```

The 24-byte manufacturer value is a whitened FastCon RF frame. The inner body
has a four-byte header and twelve encrypted bytes. Control frames use type 5,
set the forwarding bit so other lamps can relay them, and encrypt the data
with a four-byte mesh key.

## Confirmed light commands

The first command byte contains bit 7 = on and bits 0..6 = brightness 1..127.
Zero means off. A six-byte color command is:

```text
[on|brightness, blue, red, green, warm-white, cold-white]
```

The official app normalizes RGB so the three RGB bytes add to approximately
255. The brightness byte carries the separate intensity.

Physical testing on BLFL-LFCA firmware `4.4.0.3850.56` confirmed on/off,
brightness, and the red, green, and blue primaries with this layout. A 20-30 ms
advertising interval was required on the tested Realtek Linux adapter.

## Mesh key and address

Every command needs the four-byte mesh key and the light's mesh address. An
Android BRmesh log has historically exposed both after changing a light:

```text
adb logcat | grep jyq
... payload:220300000000000000000000, key: b2fd16aa
```

Here the key is `b2fd16aa`; the second payload byte (`03`) is the light ID.
Do not commit your real key or phone logs.

## Device-side timer status

The product/app advertises timing support, but none of the reviewed public
FastCon implementations documents the clock or schedule command. The timer UI
may either program persistent state in the lamp or remain app-side. It should
not be implemented by guessing packet bytes. Capture advertisements while
creating, editing, and deleting a BRmesh timer, then verify the timer still
fires with the phone off and out of BLE range. Only that proves it runs on the
light, as required by this project.

## Linux caveat

Known BRMesh lights require the BLE flags AD structure before manufacturer
data. Some BlueZ versions serialize it later, causing valid-looking packets to
be ignored. The CLI reports the frame before sending so `btmon` can verify the
actual on-air ordering. A patched BlueZ or a raw-HCI/ESP32 transmitter may be
needed if the order is wrong.

The transmission interval matters too. Linux's legacy management command chose
1280 ms on the tested Realtek adapter, while working FastCon transmitters use
roughly 20-30 ms. The CLI's `btmgmt` backend uses BlueZ's extended management
parameter command to request that faster interval while retaining legacy BLE
advertising PDUs.

## Implementation provenance

Packet construction was checked against the MIT-licensed `Fastcon.cpp` test
vectors from `logichousepcb/brmesh-hub`, which in turn credits MoodyAPI,
`dennispg/esphome-fastcon`, and `stromaa00/fastConLight`.
