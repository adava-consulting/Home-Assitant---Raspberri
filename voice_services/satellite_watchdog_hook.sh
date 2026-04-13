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

EVENT_NAME="${1:-}"

if [[ -z "$EVENT_NAME" ]] || [[ "${SATELLITE_STREAMING_TIMEOUT_SECONDS}" == "0" ]]; then
  exit 0
fi

case "$EVENT_NAME" in
  streaming_start)
    mkdir -p "$(dirname "$SATELLITE_WATCHDOG_STATE_FILE")"
    printf 'streaming_start %s\n' "$(date +%s)" > "$SATELLITE_WATCHDOG_STATE_FILE"
    ;;
  streaming_stop|transcript|error)
    rm -f "$SATELLITE_WATCHDOG_STATE_FILE"
    ;;
esac
