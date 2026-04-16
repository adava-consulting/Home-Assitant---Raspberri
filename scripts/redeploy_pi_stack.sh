#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: redeploy_pi_stack.sh <password> <host>" >&2
  exit 1
fi

PASSWORD="$1"
HOST="$2"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REMOTE_BRIDGE_DIR="/home/lucas/ha-command-bridge"
REMOTE_BOOTSTRAP_DIR="/home/lucas/homeassistant-bootstrap"
VOICE_ENV_FILE="${REMOTE_BRIDGE_DIR}/voice_services/respeaker_lite_satellite.env"
ESCAPED_PASSWORD="$(printf "%s" "$PASSWORD" | sed "s/'/'\"'\"'/g")"

expect "${PROJECT_DIR}/scripts/deploy_to_pi.expect" \
  "$PASSWORD" \
  "$HOST" \
  "$REMOTE_BRIDGE_DIR" \
  "$PROJECT_DIR"

expect "${PROJECT_DIR}/scripts/deploy_voice_services.expect" \
  "$PASSWORD" \
  "$HOST" \
  "${PROJECT_DIR}/voice_services" \
  "${REMOTE_BRIDGE_DIR}/voice_services"

expect "${PROJECT_DIR}/scripts/sync_ha_bootstrap.expect" \
  "$PASSWORD" \
  "$HOST" \
  "${PROJECT_DIR}/homeassistant_bootstrap" \
  "$REMOTE_BOOTSTRAP_DIR"

expect "${PROJECT_DIR}/scripts/ssh_command.expect" \
  "$PASSWORD" \
  "$HOST" \
  "printf '%s\n' '${ESCAPED_PASSWORD}' | sudo -S bash -lc 'if [ -f \"${VOICE_ENV_FILE}\" ]; then \
     if grep -q \"^WAKE_REFRACTORY_SECONDS=\" \"${VOICE_ENV_FILE}\"; then sed -i \"s/^WAKE_REFRACTORY_SECONDS=.*/WAKE_REFRACTORY_SECONDS=2/\" \"${VOICE_ENV_FILE}\"; else printf \"\\nWAKE_REFRACTORY_SECONDS=2\\n\" >> \"${VOICE_ENV_FILE}\"; fi; \
     if grep -q \"^MIC_SECONDS_TO_MUTE_AFTER_AWAKE_WAV=\" \"${VOICE_ENV_FILE}\"; then sed -i \"s/^MIC_SECONDS_TO_MUTE_AFTER_AWAKE_WAV=.*/MIC_SECONDS_TO_MUTE_AFTER_AWAKE_WAV=0.0/\" \"${VOICE_ENV_FILE}\"; else printf \"\\nMIC_SECONDS_TO_MUTE_AFTER_AWAKE_WAV=0.0\\n\" >> \"${VOICE_ENV_FILE}\"; fi; \
     if grep -q \"^SATELLITE_STREAMING_TIMEOUT_SECONDS=\" \"${VOICE_ENV_FILE}\"; then sed -i \"s/^SATELLITE_STREAMING_TIMEOUT_SECONDS=.*/SATELLITE_STREAMING_TIMEOUT_SECONDS=8/\" \"${VOICE_ENV_FILE}\"; else printf \"\\nSATELLITE_STREAMING_TIMEOUT_SECONDS=8\\n\" >> \"${VOICE_ENV_FILE}\"; fi; \
     if grep -q \"^SATELLITE_NO_SPEECH_TIMEOUT_SECONDS=\" \"${VOICE_ENV_FILE}\"; then sed -i \"s/^SATELLITE_NO_SPEECH_TIMEOUT_SECONDS=.*/SATELLITE_NO_SPEECH_TIMEOUT_SECONDS=0/\" \"${VOICE_ENV_FILE}\"; else printf \"\\nSATELLITE_NO_SPEECH_TIMEOUT_SECONDS=0\\n\" >> \"${VOICE_ENV_FILE}\"; fi; \
     systemctl restart wyoming-satellite.service; \
   fi; \
   cd \"${REMOTE_BRIDGE_DIR}\" && grep \"^VOICE_MODEL_FILE=\" .env && ls -l voice_model.json && docker compose ps && systemctl status wyoming-satellite.service --no-pager'"
