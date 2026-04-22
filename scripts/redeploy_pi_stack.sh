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
VOICE_SERVICES_ENV_FILE="${REMOTE_BRIDGE_DIR}/voice_services/.env"
ESCAPED_PASSWORD="$(printf "%s" "$PASSWORD" | sed "s/'/'\"'\"'/g")"
WHISPER_BEAM_SIZE="3"
WHISPER_INITIAL_PROMPT="Home automation voice commands. Transcribe speech literally and briefly. If the audio is unclear, prefer no text instead of guessing. Do not repeat phrases. Common commands: turn on the room lights. turn off the room lights. turn on the studio lights. turn off the studio lights. turn on the studio lights to 50 percent brightness. set the studio lights to 50 percent brightness. change the studio lights to blue. set the studio lights to blue. change the color of the studio lights to blue."

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
  "printf '%s\n' '${ESCAPED_PASSWORD}' | sudo -S -p '' bash -lc 'if [ -f \"${VOICE_ENV_FILE}\" ]; then \
     if grep -q \"^WAKE_REFRACTORY_SECONDS=\" \"${VOICE_ENV_FILE}\"; then sed -i \"s/^WAKE_REFRACTORY_SECONDS=.*/WAKE_REFRACTORY_SECONDS=8/\" \"${VOICE_ENV_FILE}\"; else printf \"\\nWAKE_REFRACTORY_SECONDS=8\\n\" >> \"${VOICE_ENV_FILE}\"; fi; \
     if grep -q \"^MIC_AUTO_GAIN=\" \"${VOICE_ENV_FILE}\"; then sed -i \"s/^MIC_AUTO_GAIN=.*/MIC_AUTO_GAIN=15/\" \"${VOICE_ENV_FILE}\"; else printf \"\\nMIC_AUTO_GAIN=15\\n\" >> \"${VOICE_ENV_FILE}\"; fi; \
     if grep -q \"^MIC_NOISE_SUPPRESSION=\" \"${VOICE_ENV_FILE}\"; then sed -i \"s/^MIC_NOISE_SUPPRESSION=.*/MIC_NOISE_SUPPRESSION=0/\" \"${VOICE_ENV_FILE}\"; else printf \"\\nMIC_NOISE_SUPPRESSION=0\\n\" >> \"${VOICE_ENV_FILE}\"; fi; \
     if grep -q \"^MIC_VOLUME_MULTIPLIER=\" \"${VOICE_ENV_FILE}\"; then sed -i \"s/^MIC_VOLUME_MULTIPLIER=.*/MIC_VOLUME_MULTIPLIER=4.0/\" \"${VOICE_ENV_FILE}\"; else printf \"\\nMIC_VOLUME_MULTIPLIER=4.0\\n\" >> \"${VOICE_ENV_FILE}\"; fi; \
     if grep -q \"^MIC_CHANNEL_INDEX=\" \"${VOICE_ENV_FILE}\"; then sed -i \"s/^MIC_CHANNEL_INDEX=.*/MIC_CHANNEL_INDEX=/\" \"${VOICE_ENV_FILE}\"; else printf \"\\nMIC_CHANNEL_INDEX=\\n\" >> \"${VOICE_ENV_FILE}\"; fi; \
     if grep -q \"^MIC_SECONDS_TO_MUTE_AFTER_AWAKE_WAV=\" \"${VOICE_ENV_FILE}\"; then sed -i \"s/^MIC_SECONDS_TO_MUTE_AFTER_AWAKE_WAV=.*/MIC_SECONDS_TO_MUTE_AFTER_AWAKE_WAV=0.0/\" \"${VOICE_ENV_FILE}\"; else printf \"\\nMIC_SECONDS_TO_MUTE_AFTER_AWAKE_WAV=0.0\\n\" >> \"${VOICE_ENV_FILE}\"; fi; \
     if grep -q \"^SND_VOLUME_MULTIPLIER=\" \"${VOICE_ENV_FILE}\"; then sed -i \"s/^SND_VOLUME_MULTIPLIER=.*/SND_VOLUME_MULTIPLIER=2.5/\" \"${VOICE_ENV_FILE}\"; else printf \"\\nSND_VOLUME_MULTIPLIER=2.5\\n\" >> \"${VOICE_ENV_FILE}\"; fi; \
     if grep -q \"^SND_MIXER_CARD=\" \"${VOICE_ENV_FILE}\"; then sed -i \"s/^SND_MIXER_CARD=.*/SND_MIXER_CARD=Headphones/\" \"${VOICE_ENV_FILE}\"; else printf \"\\nSND_MIXER_CARD=Headphones\\n\" >> \"${VOICE_ENV_FILE}\"; fi; \
     if grep -q \"^SND_MIXER_CONTROL=\" \"${VOICE_ENV_FILE}\"; then sed -i \"s/^SND_MIXER_CONTROL=.*/SND_MIXER_CONTROL=PCM/\" \"${VOICE_ENV_FILE}\"; else printf \"\\nSND_MIXER_CONTROL=PCM\\n\" >> \"${VOICE_ENV_FILE}\"; fi; \
     if grep -q \"^SND_MIXER_LEVEL=\" \"${VOICE_ENV_FILE}\"; then sed -i \"s/^SND_MIXER_LEVEL=.*/SND_MIXER_LEVEL=100%/\" \"${VOICE_ENV_FILE}\"; else printf \"\\nSND_MIXER_LEVEL=100%\\n\" >> \"${VOICE_ENV_FILE}\"; fi; \
     if grep -q \"^SATELLITE_STREAMING_TIMEOUT_SECONDS=\" \"${VOICE_ENV_FILE}\"; then sed -i \"s/^SATELLITE_STREAMING_TIMEOUT_SECONDS=.*/SATELLITE_STREAMING_TIMEOUT_SECONDS=8/\" \"${VOICE_ENV_FILE}\"; else printf \"\\nSATELLITE_STREAMING_TIMEOUT_SECONDS=8\\n\" >> \"${VOICE_ENV_FILE}\"; fi; \
     if grep -q \"^SATELLITE_NO_SPEECH_TIMEOUT_SECONDS=\" \"${VOICE_ENV_FILE}\"; then sed -i \"s/^SATELLITE_NO_SPEECH_TIMEOUT_SECONDS=.*/SATELLITE_NO_SPEECH_TIMEOUT_SECONDS=7/\" \"${VOICE_ENV_FILE}\"; else printf \"\\nSATELLITE_NO_SPEECH_TIMEOUT_SECONDS=7\\n\" >> \"${VOICE_ENV_FILE}\"; fi; \
     if grep -q \"^SATELLITE_TRANSCRIPT_TIMEOUT_SECONDS=\" \"${VOICE_ENV_FILE}\"; then sed -i \"s/^SATELLITE_TRANSCRIPT_TIMEOUT_SECONDS=.*/SATELLITE_TRANSCRIPT_TIMEOUT_SECONDS=12/\" \"${VOICE_ENV_FILE}\"; else printf \"\\nSATELLITE_TRANSCRIPT_TIMEOUT_SECONDS=12\\n\" >> \"${VOICE_ENV_FILE}\"; fi; \
     if grep -q \"^SATELLITE_POST_TRANSCRIPT_COOLDOWN_SECONDS=\" \"${VOICE_ENV_FILE}\"; then sed -i \"s/^SATELLITE_POST_TRANSCRIPT_COOLDOWN_SECONDS=.*/SATELLITE_POST_TRANSCRIPT_COOLDOWN_SECONDS=2/\" \"${VOICE_ENV_FILE}\"; else printf \"\\nSATELLITE_POST_TRANSCRIPT_COOLDOWN_SECONDS=2\\n\" >> \"${VOICE_ENV_FILE}\"; fi; \
     if grep -q \"^SATELLITE_FORCE_ASSIST_IDLE_ON_RECOVERY=\" \"${VOICE_ENV_FILE}\"; then sed -i \"s/^SATELLITE_FORCE_ASSIST_IDLE_ON_RECOVERY=.*/SATELLITE_FORCE_ASSIST_IDLE_ON_RECOVERY=0/\" \"${VOICE_ENV_FILE}\"; else printf \"\\nSATELLITE_FORCE_ASSIST_IDLE_ON_RECOVERY=0\\n\" >> \"${VOICE_ENV_FILE}\"; fi; \
   fi; \
   if [ -f \"${VOICE_SERVICES_ENV_FILE}\" ]; then \
     if grep -q \"^WAKE_WORD_THRESHOLD=\" \"${VOICE_SERVICES_ENV_FILE}\"; then sed -i \"s/^WAKE_WORD_THRESHOLD=.*/WAKE_WORD_THRESHOLD=0.15/\" \"${VOICE_SERVICES_ENV_FILE}\"; else printf \"\\nWAKE_WORD_THRESHOLD=0.15\\n\" >> \"${VOICE_SERVICES_ENV_FILE}\"; fi; \
     if grep -q \"^WAKE_WORD_TRIGGER_LEVEL=\" \"${VOICE_SERVICES_ENV_FILE}\"; then sed -i \"s/^WAKE_WORD_TRIGGER_LEVEL=.*/WAKE_WORD_TRIGGER_LEVEL=1/\" \"${VOICE_SERVICES_ENV_FILE}\"; else printf \"\\nWAKE_WORD_TRIGGER_LEVEL=1\\n\" >> \"${VOICE_SERVICES_ENV_FILE}\"; fi; \
     if grep -q \"^WAKE_WORD_REFRACTORY_SECONDS=\" \"${VOICE_SERVICES_ENV_FILE}\"; then sed -i \"s/^WAKE_WORD_REFRACTORY_SECONDS=.*/WAKE_WORD_REFRACTORY_SECONDS=8.0/\" \"${VOICE_SERVICES_ENV_FILE}\"; else printf \"\\nWAKE_WORD_REFRACTORY_SECONDS=8.0\\n\" >> \"${VOICE_SERVICES_ENV_FILE}\"; fi; \
     if grep -q \"^WHISPER_BEAM_SIZE=\" \"${VOICE_SERVICES_ENV_FILE}\"; then sed -i \"s/^WHISPER_BEAM_SIZE=.*/WHISPER_BEAM_SIZE=${WHISPER_BEAM_SIZE}/\" \"${VOICE_SERVICES_ENV_FILE}\"; else printf \"\\nWHISPER_BEAM_SIZE=${WHISPER_BEAM_SIZE}\\n\" >> \"${VOICE_SERVICES_ENV_FILE}\"; fi; \
     if grep -q \"^WHISPER_INITIAL_PROMPT=\" \"${VOICE_SERVICES_ENV_FILE}\"; then sed -i \"s|^WHISPER_INITIAL_PROMPT=.*|WHISPER_INITIAL_PROMPT=${WHISPER_INITIAL_PROMPT}|\" \"${VOICE_SERVICES_ENV_FILE}\"; else printf \"\\nWHISPER_INITIAL_PROMPT=${WHISPER_INITIAL_PROMPT}\\n\" >> \"${VOICE_SERVICES_ENV_FILE}\"; fi; \
   fi; \
   cd \"${REMOTE_BRIDGE_DIR}/voice_services\" && docker compose up -d openwakeword whisper piper; \
   systemctl restart wyoming-satellite.service; \
   cd \"${REMOTE_BRIDGE_DIR}\" && grep \"^VOICE_MODEL_FILE=\" .env && ls -l voice_model.json && docker compose ps && systemctl status wyoming-satellite.service --no-pager'"
