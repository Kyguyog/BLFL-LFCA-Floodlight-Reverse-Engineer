#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this capture with sudo: sudo ./capture_ios_timer.sh" >&2
    exit 1
fi

capture_path="$PWD/brmesh-timer-capture-$(date +%s).btsnoop"

echo "This will passively capture BLE for 75 seconds."
echo "After 'capture started' appears, use BRmesh on the iPhone to:"
echo "  1. Open Light 0679's timer/schedule screen."
echo "  2. Create and save one temporary OFF schedule."
echo "  3. Edit its time by one minute and save it again."
echo "  4. Delete the temporary schedule."
echo "No controller command will be sent by this script."
printf "Press Enter when the iPhone and light are ready... "
read ready

timeout 78 btmon --index hci0 --write "$capture_path" >/dev/null 2>&1 &
monitor_pid=$!
sleep 1
echo "Timer capture started: $capture_path"
timeout 75 bluetoothctl --timeout 72 scan le >/dev/null 2>&1 || true
wait "$monitor_pid" || true

chmod 0644 "$capture_path"
if [ -n "${SUDO_UID:-}" ] && [ -n "${SUDO_GID:-}" ]; then
    chown "$SUDO_UID:$SUDO_GID" "$capture_path"
fi
echo "Timer capture complete: $capture_path"
