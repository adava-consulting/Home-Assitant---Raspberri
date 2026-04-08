#!/usr/bin/env bash
set -euo pipefail

CONNECTIVITY_HOST="${CONNECTIVITY_HOST:-apigw.tuyaus.com}"
CONNECTIVITY_URL="${CONNECTIVITY_URL:-https://${CONNECTIVITY_HOST}}"
HOMEASSISTANT_DIR="${HOMEASSISTANT_DIR:-/home/lucas/homeassistant}"
MAX_WAIT_SECONDS="${MAX_WAIT_SECONDS:-900}"
INTERVAL_SECONDS="${INTERVAL_SECONDS:-5}"
LOG_TAG="${LOG_TAG:-ha-network-stabilizer}"

log() {
  local message="$1"
  logger -t "$LOG_TAG" "$message"
  printf '%s %s\n' "$(date -Is)" "$message"
}

deadline=$((SECONDS + MAX_WAIT_SECONDS))

log "Waiting for stable internet connectivity before restarting Home Assistant"

while (( SECONDS < deadline )); do
  if getent ahostsv4 "$CONNECTIVITY_HOST" >/dev/null 2>&1 \
    && curl -sS -o /dev/null --connect-timeout 5 "$CONNECTIVITY_URL"; then
    log "Connectivity check succeeded for ${CONNECTIVITY_HOST}; restarting Home Assistant"
    cd "$HOMEASSISTANT_DIR"
    docker compose up -d homeassistant >/dev/null
    docker compose restart homeassistant >/dev/null
    log "Home Assistant restart completed after stable internet"
    exit 0
  fi

  sleep "$INTERVAL_SECONDS"
done

log "Timed out after ${MAX_WAIT_SECONDS}s waiting for internet; leaving Home Assistant unchanged"
exit 0
