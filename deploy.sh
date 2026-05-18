#!/usr/bin/env bash
set -euo pipefail

HA_HOST="homeassistant.local"
HA_USER="root"
SRC="$(dirname "$0")/custom_components/wavin_ahc9000/"
DST="/config/custom_components/wavin_ahc9000/"

echo "Syncing integration files..."
rsync -av --delete "$SRC" "${HA_USER}@${HA_HOST}:${DST}"

echo "Restarting Home Assistant core..."
ssh "${HA_USER}@${HA_HOST}" "ha core restart"

echo "Done. Watch logs at: http://${HA_HOST}:8123/config/logs"
