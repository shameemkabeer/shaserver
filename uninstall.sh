#!/usr/bin/env bash
# Uninstall PiBoard.
#   ./uninstall.sh            stop & remove PiBoard (apps keep running, data kept)
#   ./uninstall.sh --apps     also stop & remove all apps installed through PiBoard (data kept)
#   ./uninstall.sh --purge    --apps + delete ./apps and ./data (asks first)
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
APPS=0; PURGE=0
for a in "$@"; do case "$a" in --apps) APPS=1;; --purge) APPS=1; PURGE=1;; *) echo "unknown option $a"; exit 1;; esac; done

if systemctl list-unit-files piboard-kiosk.service >/dev/null 2>&1 && \
   systemctl list-unit-files piboard-kiosk.service | grep -q piboard-kiosk; then
  echo "==> Kiosk service found; removing it (sudo)"
  sudo ./kiosk/uninstall-kiosk.sh
fi

if [ "$APPS" = 1 ]; then
  for f in apps/*/docker-compose.yml; do
    [ -e "$f" ] || continue
    d="$(dirname "$f")"; n="$(basename "$d")"
    echo "==> Removing app $n"
    docker compose --project-directory "$d" -f "$f" -p "piboard-$n" down || true
  done
fi

if [ "$PURGE" = 1 ]; then
  read -r -p "Delete ALL app data in $(pwd)/apps and settings in $(pwd)/data? Type 'delete' to confirm: " c
  if [ "$c" = "delete" ]; then
    # app containers may have created root-owned files
    docker run --rm --network none --user 0 -v "$(pwd)":/w --entrypoint rm piboard:local -rf /w/apps /w/data
    PURGED=1
    echo "==> Data deleted"
  else
    echo "==> Kept data"
  fi
fi

echo "==> Removing PiBoard container and image"
docker compose down --rmi local || true
if [ "${PURGED:-0}" = 1 ]; then rm -f .env; fi

if [ -f /etc/sysctl.d/99-piboard.conf ]; then
  echo "Note: /etc/sysctl.d/99-piboard.conf (ip_nonlocal_bind) was left in place; remove with: sudo rm /etc/sysctl.d/99-piboard.conf"
fi
echo "==> PiBoard uninstalled. You can delete this folder now."
