#!/usr/bin/env bash
# Install (or re-apply) the PiBoard kiosk: cage + Chromium on tty1, started at
# boot, no desktop. Safe to re-run: it also applies kiosk/touch.conf (touch
# calibration written by tools/calibrate-touch.py) as a udev rule.
# Usage: sudo ./kiosk/install-kiosk.sh            (user = the one who ran sudo)
#        sudo KIOSK_USER=bob ./kiosk/install-kiosk.sh
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo"; exit 1; }
DIR="$(dirname "$(readlink -f "$0")")"
KUSER="${KIOSK_USER:-${SUDO_USER:-}}"
[ -n "$KUSER" ] && [ "$KUSER" != root ] || { echo "Set KIOSK_USER=<your user>"; exit 1; }
RULE=/etc/udev/rules.d/99-piboard-touch.rules

PKGS="cage chromium wlr-randr curl udisks2"   # udisks2: USB drives (kiosk/hostd.py)
if ! dpkg -s $PKGS >/dev/null 2>&1; then
  echo "==> Installing $PKGS (no desktop environment)"
  apt-get update
  apt-get install -y --no-install-recommends $PKGS
fi

echo "==> Making sure $KUSER can use the GPU and input devices"
usermod -aG video,render,input "$KUSER"

if [ ! -f "$DIR/kiosk.conf" ]; then
  install -o "$KUSER" -g "$(id -gn "$KUSER")" -m 644 "$DIR/kiosk.conf.example" "$DIR/kiosk.conf"
fi

# Touch calibration. The matrix is validated as six numbers before it goes into
# a root-owned udev rule (kiosk/touch.conf is user-writable).
if [ -f "$DIR/touch.conf" ]; then
  M="$(sed -n 's/^TOUCH_CALIBRATION="\(.*\)"$/\1/p' "$DIR/touch.conf")"
  NUM='-?[0-9]+(\.[0-9]+)?'
  if [[ "$M" =~ ^$NUM( $NUM){5}$ ]]; then
    echo "==> Touch calibration: $M"
    cat > "$RULE" <<RULE
# PiBoard: touch calibration for the Waveshare 3.5in HDMI LCD (XPT2046/ADS7846)
ACTION=="add|change", KERNEL=="event*", ATTRS{name}=="ADS7846 Touchscreen", ENV{LIBINPUT_CALIBRATION_MATRIX}="$M"
RULE
    udevadm control --reload
    udevadm trigger --action=change --subsystem-match=input
  else
    echo "!! $DIR/touch.conf has an invalid TOUCH_CALIBRATION; skipped" >&2
  fi
fi

chmod +x "$DIR/start-kiosk.sh" "$DIR/session.sh" "$DIR/hostd.py"
cat > /etc/systemd/system/piboard-kiosk.service <<UNIT
[Unit]
Description=PiBoard kiosk (cage + Chromium on tty1)
After=systemd-user-sessions.service docker.service network-online.target getty@tty1.service
Wants=network-online.target
Conflicts=getty@tty1.service

[Service]
Type=simple
User=$KUSER
PAMName=login
TTYPath=/dev/tty1
TTYReset=yes
TTYVHangup=yes
TTYVTDisallocate=yes
StandardInput=tty-fail
StandardOutput=journal
StandardError=journal
UtmpIdentifier=tty1
UtmpMode=user
Environment=XDG_SESSION_TYPE=wayland
ExecStart=$DIR/start-kiosk.sh
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl disable getty@tty1.service >/dev/null 2>&1 || true
systemctl enable piboard-kiosk.service
if systemctl is-active --quiet piboard-kiosk.service; then
  echo "==> Restarting the kiosk to pick up changes"
  systemctl restart piboard-kiosk.service
fi
echo "==> Done. Start now with: sudo systemctl start piboard-kiosk   Logs: journalctl -u piboard-kiosk -f"
