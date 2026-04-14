#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${VOICE_SATELLITE_ENV_FILE:-$SCRIPT_DIR/respeaker_lite_satellite.env}"
PROJECT_ENV_FILE="${PROJECT_ENV_FILE:-$(cd "$SCRIPT_DIR/.." && pwd)/.env}"

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
fi

if [[ -f "$PROJECT_ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$PROJECT_ENV_FILE"
fi

: "${SATELLITE_STREAMING_TIMEOUT_SECONDS:=20}"
: "${SATELLITE_WATCHDOG_STATE_FILE:=/tmp/wyoming-satellite-watchdog.state}"
: "${SATELLITE_NAME:=respeaker-lite}"
: "${SATELLITE_NO_SPEECH_TIMEOUT_SECONDS:=0}"
: "${SATELLITE_NO_SPEECH_STATE_FILE:=/tmp/wyoming-satellite-no-speech.state}"
: "${SATELLITE_NO_SPEECH_RESTART_COMMAND:=pkill -TERM -f \"/opt/wyoming-satellite/script/run --name ${SATELLITE_NAME}\"}"
: "${ASSIST_SATELLITE_ENTITY_ID:=assist_satellite.respeaker_lite}"
: "${SATELLITE_EVENT_LOG_FILE:=}"
: "${SATELLITE_ON_DETECTION_COMMAND:=}"
: "${SATELLITE_ON_STREAMING_START_COMMAND:=}"
: "${SATELLITE_ON_STREAMING_STOP_COMMAND:=}"
: "${SATELLITE_ON_TRANSCRIPT_COMMAND:=}"
: "${SATELLITE_ON_STT_START_COMMAND:=}"
: "${SATELLITE_ON_STT_STOP_COMMAND:=}"
: "${SATELLITE_ON_ERROR_COMMAND:=}"

EVENT_NAME="${1:-}"

if [[ -z "$EVENT_NAME" ]]; then
  exit 0
fi

log_event() {
  if [[ -z "$SATELLITE_EVENT_LOG_FILE" ]]; then
    return 0
  fi

  mkdir -p "$(dirname "$SATELLITE_EVENT_LOG_FILE")"
  printf '%s %s\n' "$(date -Iseconds)" "$EVENT_NAME" >> "$SATELLITE_EVENT_LOG_FILE"
}

run_custom_command() {
  local command=""
  case "$EVENT_NAME" in
    detection)
      command="$SATELLITE_ON_DETECTION_COMMAND"
      ;;
    streaming_start)
      command="$SATELLITE_ON_STREAMING_START_COMMAND"
      ;;
    streaming_stop)
      command="$SATELLITE_ON_STREAMING_STOP_COMMAND"
      ;;
    transcript)
      command="$SATELLITE_ON_TRANSCRIPT_COMMAND"
      ;;
    stt_start)
      command="$SATELLITE_ON_STT_START_COMMAND"
      ;;
    stt_stop)
      command="$SATELLITE_ON_STT_STOP_COMMAND"
      ;;
    error)
      command="$SATELLITE_ON_ERROR_COMMAND"
      ;;
  esac

  if [[ -n "$command" ]]; then
    bash -lc "$command" >/dev/null 2>&1 || true
  fi
}

log_event
run_custom_command

clear_no_speech_state() {
  rm -f "$SATELLITE_NO_SPEECH_STATE_FILE"
}

force_assist_satellite_idle() {
  if [[ -z "${HOME_ASSISTANT_URL:-}" ]] || [[ -z "${HOME_ASSISTANT_TOKEN:-}" ]] || [[ -z "${ASSIST_SATELLITE_ENTITY_ID:-}" ]]; then
    return 0
  fi

  curl -sS \
    -X POST \
    -H "Authorization: Bearer ${HOME_ASSISTANT_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "{\"entity_id\":\"${ASSIST_SATELLITE_ENTITY_ID}\",\"message\":\"\",\"preannounce\":false}" \
    "${HOME_ASSISTANT_URL}/api/services/assist_satellite/announce" >/dev/null 2>&1 || true
}

start_no_speech_timer() {
  if [[ "${SATELLITE_NO_SPEECH_TIMEOUT_SECONDS}" == "0" ]]; then
    return 0
  fi

  mkdir -p "$(dirname "$SATELLITE_NO_SPEECH_STATE_FILE")"
  local token
  token="$(date +%s.%N)-$$"
  printf '%s\n' "$token" > "$SATELLITE_NO_SPEECH_STATE_FILE"

  (
    sleep "$SATELLITE_NO_SPEECH_TIMEOUT_SECONDS"
    if [[ ! -f "$SATELLITE_NO_SPEECH_STATE_FILE" ]]; then
      exit 0
    fi

    local current_token
    current_token="$(cat "$SATELLITE_NO_SPEECH_STATE_FILE" 2>/dev/null || true)"
    if [[ "$current_token" != "$token" ]]; then
      exit 0
    fi

    rm -f "$SATELLITE_NO_SPEECH_STATE_FILE"
    logger -t wyoming-satellite "No speech detected ${SATELLITE_NO_SPEECH_TIMEOUT_SECONDS}s after wake word; restarting satellite"
    force_assist_satellite_idle
    bash -lc "$SATELLITE_NO_SPEECH_RESTART_COMMAND" >/dev/null 2>&1 || true
  ) >/dev/null 2>&1 &
}

case "$EVENT_NAME" in
  detection)
    start_no_speech_timer
    ;;
  streaming_start)
    if [[ "${SATELLITE_STREAMING_TIMEOUT_SECONDS}" != "0" ]]; then
      mkdir -p "$(dirname "$SATELLITE_WATCHDOG_STATE_FILE")"
      printf 'streaming_start %s\n' "$(date +%s)" > "$SATELLITE_WATCHDOG_STATE_FILE"
    fi
    ;;
  stt_start)
    clear_no_speech_state
    ;;
  streaming_stop|transcript|error)
    clear_no_speech_state
    ;;
  stt_stop)
    ;;
esac

case "$EVENT_NAME" in
  streaming_stop|transcript|error)
    if [[ "${SATELLITE_STREAMING_TIMEOUT_SECONDS}" != "0" ]]; then
      rm -f "$SATELLITE_WATCHDOG_STATE_FILE"
    fi
    ;;
esac
