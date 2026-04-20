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
: "${SATELLITE_NO_SPEECH_TIMEOUT_SECONDS:=7}"
: "${SATELLITE_NO_SPEECH_STATE_FILE:=/tmp/wyoming-satellite-no-speech.state}"
: "${SATELLITE_NO_SPEECH_RESTART_COMMAND:=pkill -TERM -f \"/opt/wyoming-satellite/script/run --name ${SATELLITE_NAME}\"}"
: "${SATELLITE_TRANSCRIPT_TIMEOUT_SECONDS:=12}"
: "${SATELLITE_TRANSCRIPT_TIMEOUT_STATE_FILE:=/tmp/wyoming-satellite-transcript.state}"
: "${SATELLITE_TRANSCRIPT_TIMEOUT_RESTART_COMMAND:=${SATELLITE_NO_SPEECH_RESTART_COMMAND}}"
: "${SATELLITE_POST_TRANSCRIPT_COOLDOWN_SECONDS:=6}"
: "${SATELLITE_POST_TRANSCRIPT_STATE_FILE:=/tmp/wyoming-satellite-post-transcript.state}"
: "${SATELLITE_POST_TRANSCRIPT_RESTART_COMMAND:=${SATELLITE_NO_SPEECH_RESTART_COMMAND}}"
: "${SATELLITE_FORCE_ASSIST_IDLE_ON_RECOVERY:=0}"
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

clear_transcript_timeout_state() {
  rm -f "$SATELLITE_TRANSCRIPT_TIMEOUT_STATE_FILE"
}

clear_post_transcript_state() {
  rm -f "$SATELLITE_POST_TRANSCRIPT_STATE_FILE"
}

force_assist_satellite_idle() {
  if [[ "${SATELLITE_FORCE_ASSIST_IDLE_ON_RECOVERY}" != "1" ]]; then
    return 0
  fi

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
    clear_transcript_timeout_state
    logger -t wyoming-satellite "No speech detected ${SATELLITE_NO_SPEECH_TIMEOUT_SECONDS}s after wake word; restarting satellite"
    force_assist_satellite_idle
    bash -lc "$SATELLITE_NO_SPEECH_RESTART_COMMAND" >/dev/null 2>&1 || true
  ) >/dev/null 2>&1 &
}

start_transcript_timeout_timer() {
  if [[ "${SATELLITE_TRANSCRIPT_TIMEOUT_SECONDS}" == "0" ]]; then
    clear_transcript_timeout_state
    return 0
  fi

  mkdir -p "$(dirname "$SATELLITE_TRANSCRIPT_TIMEOUT_STATE_FILE")"
  local token
  token="$(date +%s.%N)-$$"
  printf '%s\n' "$token" > "$SATELLITE_TRANSCRIPT_TIMEOUT_STATE_FILE"

  (
    sleep "$SATELLITE_TRANSCRIPT_TIMEOUT_SECONDS"
    if [[ ! -f "$SATELLITE_TRANSCRIPT_TIMEOUT_STATE_FILE" ]]; then
      exit 0
    fi

    local current_token
    current_token="$(cat "$SATELLITE_TRANSCRIPT_TIMEOUT_STATE_FILE" 2>/dev/null || true)"
    if [[ "$current_token" != "$token" ]]; then
      exit 0
    fi

    rm -f "$SATELLITE_TRANSCRIPT_TIMEOUT_STATE_FILE"
    clear_no_speech_state
    clear_post_transcript_state
    logger -t wyoming-satellite "Transcript not received ${SATELLITE_TRANSCRIPT_TIMEOUT_SECONDS}s after stt_stop; restarting satellite"
    force_assist_satellite_idle
    bash -lc "$SATELLITE_TRANSCRIPT_TIMEOUT_RESTART_COMMAND" >/dev/null 2>&1 || true
  ) >/dev/null 2>&1 &
}

mark_post_transcript_cooldown() {
  if [[ "${SATELLITE_POST_TRANSCRIPT_COOLDOWN_SECONDS}" == "0" ]]; then
    clear_post_transcript_state
    return 0
  fi

  mkdir -p "$(dirname "$SATELLITE_POST_TRANSCRIPT_STATE_FILE")"
  date +%s > "$SATELLITE_POST_TRANSCRIPT_STATE_FILE"
}

restart_if_detection_is_in_post_transcript_cooldown() {
  if [[ "${SATELLITE_POST_TRANSCRIPT_COOLDOWN_SECONDS}" == "0" ]]; then
    return 1
  fi

  if [[ ! -f "$SATELLITE_POST_TRANSCRIPT_STATE_FILE" ]]; then
    return 1
  fi

  local transcript_ts now age
  transcript_ts="$(cat "$SATELLITE_POST_TRANSCRIPT_STATE_FILE" 2>/dev/null || true)"
  now="$(date +%s)"

  if [[ -z "$transcript_ts" ]] || ! [[ "$transcript_ts" =~ ^[0-9]+$ ]]; then
    clear_post_transcript_state
    return 1
  fi

  age=$(( now - transcript_ts ))
  if (( age >= SATELLITE_POST_TRANSCRIPT_COOLDOWN_SECONDS )); then
    clear_post_transcript_state
    return 1
  fi

  clear_post_transcript_state
  clear_no_speech_state
  clear_transcript_timeout_state
  logger -t wyoming-satellite \
    "Wake detected ${age}s after transcript; restarting satellite to avoid self-trigger"
  force_assist_satellite_idle
  bash -lc "$SATELLITE_POST_TRANSCRIPT_RESTART_COMMAND" >/dev/null 2>&1 || true
  return 0
}

case "$EVENT_NAME" in
  detection)
    clear_transcript_timeout_state
    if restart_if_detection_is_in_post_transcript_cooldown; then
      exit 0
    fi
    start_no_speech_timer
    ;;
  streaming_start)
    clear_transcript_timeout_state
    if [[ "${SATELLITE_STREAMING_TIMEOUT_SECONDS}" != "0" ]]; then
      mkdir -p "$(dirname "$SATELLITE_WATCHDOG_STATE_FILE")"
      printf 'streaming_start %s\n' "$(date +%s)" > "$SATELLITE_WATCHDOG_STATE_FILE"
    fi
    ;;
  stt_start)
    clear_no_speech_state
    clear_transcript_timeout_state
    ;;
  transcript)
    clear_no_speech_state
    clear_transcript_timeout_state
    mark_post_transcript_cooldown
    ;;
  streaming_stop|error)
    clear_no_speech_state
    clear_transcript_timeout_state
    ;;
  stt_stop)
    start_transcript_timeout_timer
    ;;
esac

case "$EVENT_NAME" in
  streaming_stop|transcript|error)
    if [[ "${SATELLITE_STREAMING_TIMEOUT_SECONDS}" != "0" ]]; then
      rm -f "$SATELLITE_WATCHDOG_STATE_FILE"
    fi
    ;;
esac
