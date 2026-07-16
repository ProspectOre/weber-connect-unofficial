#!/usr/bin/with-contenv bashio
set -euo pipefail

CONFIG_DIR="/data/weber-connect-bridge"
MQTT_CREDENTIALS_FILE="${CONFIG_DIR}/mqtt_credentials.json"

mkdir -p "${CONFIG_DIR}"
chmod 700 "${CONFIG_DIR}"

get_config() {
    local value
    value="$(bashio::config "$1" 2>/dev/null || true)"
    if [[ "${value}" == "null" ]]; then
        value=""
    fi
    printf '%s' "${value}"
}

LOG_LEVEL="$(get_config 'log_level')"
LOG_LEVEL="${LOG_LEVEL:-info}"

MQTT_HOST="$(get_config 'mqtt.host')"
MQTT_PORT="$(get_config 'mqtt.port')"
MQTT_USERNAME="$(get_config 'mqtt.username')"
MQTT_PASSWORD="$(get_config 'mqtt.password')"

if [[ -z "${MQTT_HOST}" ]]; then
    MQTT_HOST="$(bashio::services mqtt 'host' 2>/dev/null || true)"
    MQTT_PORT="$(bashio::services mqtt 'port' 2>/dev/null || true)"
    MQTT_USERNAME="$(bashio::services mqtt 'username' 2>/dev/null || true)"
    MQTT_PASSWORD="$(bashio::services mqtt 'password' 2>/dev/null || true)"
fi

MQTT_ARGS=()
if [[ -n "${MQTT_HOST}" ]]; then
    MQTT_ARGS=(--mqtt-host "${MQTT_HOST}" --mqtt-port "${MQTT_PORT:-1883}")
    if [[ -n "${MQTT_USERNAME}" || -n "${MQTT_PASSWORD}" ]]; then
        MQTT_USERNAME_VALUE="${MQTT_USERNAME}" \
            MQTT_PASSWORD_VALUE="${MQTT_PASSWORD}" \
            MQTT_CREDENTIALS_FILE="${MQTT_CREDENTIALS_FILE}" \
            python3 - <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ["MQTT_CREDENTIALS_FILE"])
username = os.environ.get("MQTT_USERNAME_VALUE", "")
password = os.environ.get("MQTT_PASSWORD_VALUE", "")
if username and not password:
    raise SystemExit("MQTT username was provided without MQTT password")
if password and not username:
    raise SystemExit("MQTT password was provided without MQTT username")
path.write_text(json.dumps({"username": username, "password": password}), encoding="utf-8")
path.chmod(0o600)
PY
        MQTT_ARGS+=(--mqtt-credentials-file "${MQTT_CREDENTIALS_FILE}")
    fi
else
    bashio::log.warning "MQTT is not configured yet; probes will not appear as Home Assistant entities until Mosquitto is available or mqtt.host is set."
fi
unset MQTT_USERNAME MQTT_PASSWORD

bashio::log.info "Starting Weber Connect panel"
exec python3 /app/weber_panel.py \
    --port 8099 \
    --data-dir "${CONFIG_DIR}" \
    --log-level "${LOG_LEVEL}" \
    "${MQTT_ARGS[@]}"
