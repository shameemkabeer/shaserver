#!/usr/bin/env bash
# Update PiBoard itself. Installed apps are updated from the Apps page (Update button).
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
if [ -d .git ]; then
  echo "==> Pulling latest code"
  git pull --ff-only
fi
echo "==> Rebuilding (with fresh base images)"
docker compose build --pull
docker compose up -d
docker image prune -f >/dev/null
echo "==> Done. Settings, password and apps in ./data and ./apps are untouched."
if systemctl is-active --quiet piboard-kiosk 2>/dev/null; then
  echo "    Kiosk will reconnect on its own; to force it: sudo systemctl restart piboard-kiosk"
fi
