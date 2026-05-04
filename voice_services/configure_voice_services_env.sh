#!/usr/bin/env bash
set -euo pipefail

VOICE_SERVICES_DIR="${VOICE_SERVICES_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
BRIDGE_DIR="${BRIDGE_DIR:-$(cd "${VOICE_SERVICES_DIR}/.." && pwd)}"
BRIDGE_ENV_FILE="${BRIDGE_ENV_FILE:-${BRIDGE_DIR}/.env}"
VOICE_SERVICES_ENV_FILE="${VOICE_SERVICES_ENV_FILE:-${VOICE_SERVICES_DIR}/.env}"

set_env_line() {
  local key="$1"
  local value="$2"
  local escaped_value

  mkdir -p "$(dirname "${VOICE_SERVICES_ENV_FILE}")"
  touch "${VOICE_SERVICES_ENV_FILE}"

  escaped_value="$(printf '%s' "${value}" | sed -e 's/[\/&|]/\\&/g')"
  if grep -q "^${key}=" "${VOICE_SERVICES_ENV_FILE}"; then
    sed -i.bak "s|^${key}=.*|${key}=${escaped_value}|" "${VOICE_SERVICES_ENV_FILE}"
    rm -f "${VOICE_SERVICES_ENV_FILE}.bak"
  else
    printf '%s=%s\n' "${key}" "${value}" >>"${VOICE_SERVICES_ENV_FILE}"
  fi
}

read_bridge_env() {
  local key="$1"

  if [[ ! -f "${BRIDGE_ENV_FILE}" ]]; then
    return 0
  fi

  grep -E "^${key}=" "${BRIDGE_ENV_FILE}" | tail -n 1 | cut -d= -f2- || true
}

home_assistant_ws_uri() {
  local url="$1"

  url="${url%/}"
  case "${url}" in
    http://*)
      printf 'ws://%s/api/websocket' "${url#http://}"
      ;;
    https://*)
      printf 'wss://%s/api/websocket' "${url#https://}"
      ;;
    ws://*|wss://*)
      if [[ "${url}" == */api/websocket ]]; then
        printf '%s' "${url}"
      else
        printf '%s/api/websocket' "${url}"
      fi
      ;;
    *)
      printf 'ws://host.docker.internal:8123/api/websocket'
      ;;
  esac
}

hass_url="$(read_bridge_env HOME_ASSISTANT_URL)"
hass_token="$(read_bridge_env HOME_ASSISTANT_TOKEN)"

set_env_line STT_ENGINE speech_to_phrase
set_env_line SPEECH_TO_PHRASE_HASS_WEBSOCKET_URI "$(home_assistant_ws_uri "${hass_url}")"
set_env_line SPEECH_TO_PHRASE_CUSTOM_SENTENCES_DIR /home/lucas/homeassistant-bootstrap/custom_sentences

if [[ -n "${hass_token}" ]]; then
  set_env_line SPEECH_TO_PHRASE_HASS_TOKEN "${hass_token}"
elif ! grep -q '^SPEECH_TO_PHRASE_HASS_TOKEN=.' "${VOICE_SERVICES_ENV_FILE}" 2>/dev/null; then
  set_env_line SPEECH_TO_PHRASE_HASS_TOKEN ""
  echo "warning: HOME_ASSISTANT_TOKEN was not found; speech-to-phrase cannot train until SPEECH_TO_PHRASE_HASS_TOKEN is set" >&2
fi

set_env_line WAKE_WORD_MODEL "${WAKE_WORD_MODEL:-hey_jarvis}"
set_env_line WAKE_WORD_THRESHOLD "${WAKE_WORD_THRESHOLD:-0.18}"
set_env_line WAKE_WORD_TRIGGER_LEVEL "${WAKE_WORD_TRIGGER_LEVEL:-1}"
set_env_line WAKE_WORD_REFRACTORY_SECONDS "${WAKE_WORD_REFRACTORY_SECONDS:-8.0}"

# Keep Whisper configured as an opt-in fallback on host port 10301.
set_env_line WHISPER_MODEL "${WHISPER_MODEL:-base-int8}"
set_env_line WHISPER_LANGUAGE "${WHISPER_LANGUAGE:-en}"
set_env_line WHISPER_BEAM_SIZE "${WHISPER_BEAM_SIZE:-1}"
set_env_line WHISPER_CPU_THREADS "${WHISPER_CPU_THREADS:-2}"
set_env_line WHISPER_INITIAL_PROMPT "${WHISPER_INITIAL_PROMPT:-}"
set_env_line WHISPER_VAD_MIN_SPEECH_MS "${WHISPER_VAD_MIN_SPEECH_MS:-200}"
set_env_line WHISPER_VAD_MIN_SILENCE_MS "${WHISPER_VAD_MIN_SILENCE_MS:-700}"

echo "voice_services_env_configured=${VOICE_SERVICES_ENV_FILE}"
