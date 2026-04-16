#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${VOICE_SATELLITE_ENV_FILE:-$SCRIPT_DIR/respeaker_lite_satellite.env}"

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
fi

: "${SATELLITE_STREAMING_TIMEOUT_SECONDS:=20}"
: "${SATELLITE_WATCHDOG_STATE_FILE:=/tmp/wyoming-satellite-watchdog.state}"
: "${SATELLITE_WATCHDOG_LOG_TAG:=wyoming-satellite-watchdog}"
: "${SATELLITE_WATCHDOG_RESTART_CMD:=systemctl restart wyoming-satellite.service}"

if [[ "${SATELLITE_STREAMING_TIMEOUT_SECONDS}" == "0" ]] || [[ ! -f "$SATELLITE_WATCHDOG_STATE_FILE" ]]; then
  exit 0
fi

read -r event_name started_at < "$SATELLITE_WATCHDOG_STATE_FILE" || exit 0

if [[ "$event_name" != "streaming_start" ]] || [[ -z "${started_at:-}" ]]; then
  rm -f "$SATELLITE_WATCHDOG_STATE_FILE"
  exit 0
fi

now_epoch="$(date +%s)"
age_seconds="$((now_epoch - started_at))"

if (( age_seconds < SATELLITE_STREAMING_TIMEOUT_SECONDS )); then
  exit 0
fi

logger -t "$SATELLITE_WATCHDOG_LOG_TAG" \
  "Streaming state is stale after ${age_seconds}s; restarting wyoming-satellite.service"
rm -f "$SATELLITE_WATCHDOG_STATE_FILE"
exec $SATELLITE_WATCHDOG_RESTART_CMD
