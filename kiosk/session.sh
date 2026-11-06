#!/usr/bin/env bash
# Runs INSIDE cage (started by start-kiosk.sh). Applies the display mode, then
# keeps Chromium running. Killing Chromium (pkill -f piboard-kiosk-browser)
# re-reads kiosk.conf, so URL/zoom/mode can be changed without sudo.
set -u
DIR="$(dirname "$(readlink -f "$0")")"
ROOT="$(dirname "$DIR")"
BROWSER="$(command -v chromium || command -v chromium-browser)"
PROFILE="${XDG_RUNTIME_DIR:-/tmp}/piboard-kiosk-browser"

apply_mode() {
  command -v wlr-randr >/dev/null || return 0
  local out="${KIOSK_OUTPUT:-}"
  [ -n "$out" ] || out="$(wlr-randr 2>/dev/null | awk '/^[^ ]/{print $1; exit}')"
  [ -n "$out" ] || return 0
  case "${KIOSK_MODE:-}" in
    "") ;;
    custom:*) wlr-randr --output "$out" --custom-mode "${KIOSK_MODE#custom:}" ;;
    *)        wlr-randr --output "$out" --mode "$KIOSK_MODE" ;;
  esac
  [ -n "${KIOSK_TRANSFORM:-}" ] && wlr-randr --output "$out" --transform "$KIOSK_TRANSFORM"
  return 0
}

# Host helper: display on/off (schedule, touch to wake) and USB drive mounting.
# It must run in this (seat) session for Wayland and udisks to allow it.
if command -v python3 >/dev/null && [ -f "$DIR/hostd.py" ]; then
  python3 -I "$DIR/hostd.py" >/dev/null 2>&1 &
fi

LAST_MODE="unset"
while :; do
  KIOSK_MODE="" KIOSK_TRANSFORM="" KIOSK_SCALE=1 KIOSK_URL="" CHROMIUM_EXTRA_ARGS=""
  # shellcheck disable=SC1091
  [ -f "$DIR/kiosk.conf" ] && . "$DIR/kiosk.conf"
  if [ "$LAST_MODE" != "${KIOSK_MODE}|${KIOSK_TRANSFORM}" ]; then
    apply_mode; LAST_MODE="${KIOSK_MODE}|${KIOSK_TRANSFORM}"; sleep 0.5
  fi
  URL="${KIOSK_URL:-}"
  if [ -z "$URL" ]; then
    TOKEN="$(cat "$ROOT/data/kiosk.token" 2>/dev/null || true)"
    # Token in the URL fragment: never sent to the server or logged; the page
    # swaps it for a read-only session cookie and clears it from the URL.
    URL="http://127.0.0.1:${PIBOARD_PORT:-8088}/kiosk#$TOKEN"
  fi
  rm -rf "$PROFILE"
  # shellcheck disable=SC2086
  "$BROWSER" --user-data-dir="$PROFILE" --class=piboard-kiosk-browser \
    --kiosk --incognito --no-first-run --noerrdialogs --disable-infobars \
    --disable-session-crashed-bubble --disable-translate --disable-features=TranslateUI \
    --overscroll-history-navigation=0 --disable-pinch --password-store=basic \
    --check-for-update-interval=31536000 --ozone-platform=wayland \
    --touch-events=enabled --allow-file-access-from-files \
    --force-device-scale-factor="$KIOSK_SCALE" $CHROMIUM_EXTRA_ARGS "$URL"
  sleep 1
done
