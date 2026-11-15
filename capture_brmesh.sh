#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this diagnostic with sudo: sudo ./capture_brmesh.sh" >&2
    exit 1
fi

capture_path="$PWD/brmesh-capture-$(date +%s).btsnoop"

echo "Capturing Bluetooth traffic to $capture_path"
timeout 9 btmon --index hci0 --write "$capture_path" >/dev/null 2>&1 &
monitor_pid=$!
sleep 1

python3 "$PWD/brmesh.py" \
    --backend btmgmt \
    --ble-flags 0x02 \
    --key 72683670 \
    --id 1 \
    --duration 3 \
    off

wait "$monitor_pid" || true
chmod 0644 "$capture_path"
if [ -n "${SUDO_UID:-}" ] && [ -n "${SUDO_GID:-}" ]; then
    chown "$SUDO_UID:$SUDO_GID" "$capture_path"
fi
echo "Capture complete: $capture_path"
