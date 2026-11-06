#!/usr/bin/env bash
# Launched by piboard-kiosk.service on tty1: waits for PiBoard, then runs the
# kiosk session (session.sh -> Chromium) inside the cage Wayland compositor.
set -u
DIR="$(dirname "$(readlink -f "$0")")"
ROOT="$(dirname "$DIR")"
CAGE_ARGS="-d -s"
# shellcheck disable=SC1091
[ -f "$DIR/kiosk.conf" ] && . "$DIR/kiosk.conf"
PIBOARD_PORT="$(grep -E '^PIBOARD_PORT=' "$ROOT/.env" 2>/dev/null | cut -d= -f2)"
export PIBOARD_PORT="${PIBOARD_PORT:-8088}"

if [ -z "${KIOSK_URL:-}" ]; then
  for _ in $(seq 1 120); do
    curl -fsS "http://127.0.0.1:$PIBOARD_PORT/api/health" >/dev/null 2>&1 && break
    sleep 1
  done
fi
# shellcheck disable=SC2086
exec cage $CAGE_ARGS -- "$DIR/session.sh"
