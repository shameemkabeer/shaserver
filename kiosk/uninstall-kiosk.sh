#!/usr/bin/env bash
# Remove the PiBoard kiosk service and restore the normal console on tty1.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo"; exit 1; }
systemctl disable --now piboard-kiosk.service 2>/dev/null || true
rm -f /etc/systemd/system/piboard-kiosk.service /etc/udev/rules.d/99-piboard-touch.rules
systemctl daemon-reload
systemctl enable --now getty@tty1.service
echo "Kiosk removed. cage/chromium packages were kept; remove with: sudo apt purge cage chromium"
