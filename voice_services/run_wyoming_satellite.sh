#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${VOICE_SATELLITE_ENV_FILE:-$SCRIPT_DIR/respeaker_lite_satellite.env}"

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
fi

: "${SATELLITE_NAME:=respeaker-lite}"
: "${SATELLITE_URI:=tcp://0.0.0.0:10700}"
: "${WYOMING_SATELLITE_DIR:=/opt/wyoming-satellite}"
: "${WAKE_URI:=tcp://127.0.0.1:10400}"
: "${WAKE_WORD_NAME:=ok_nabu}"
: "${MIC_DEVICE_HINT:=ReSpeaker Lite}"
: "${SND_DEVICE_HINT:=ReSpeaker Lite}"
: "${MIC_AUTO_GAIN:=5}"
: "${MIC_NOISE_SUPPRESSION:=2}"
: "${MIC_VOLUME_MULTIPLIER:=1.0}"
: "${SND_VOLUME_MULTIPLIER:=1.0}"
: "${SATELLITE_WAIT_FOR_DEVICE_SECONDS:=5}"
: "${SATELLITE_DEBUG:=0}"

find_alsa_device() {
  local tool="$1"
  local hint="$2"

  "$tool" -L 2>/dev/null | awk 'NF {print}' | python3 - "$hint" <<'PY'
import sys

hint = sys.argv[1].strip().lower()
devices = [line.strip() for line in sys.stdin if line.strip()]
matches = [device for device in devices if hint in device.lower()]
preferred = [device for device in matches if device.startswith("plughw:")]
selected = preferred[0] if preferred else (matches[0] if matches else "")
print(selected)
PY
}

wait_for_device() {
  local tool="$1"
  local hint="$2"
  local label="$3"

  local device=""
  while [[ -z "$device" ]]; do
    device="$(find_alsa_device "$tool" "$hint")"
    if [[ -n "$device" ]]; then
      echo "Detected $label device: $device"
      printf '%s\n' "$device"
      return 0
    fi

    echo "Waiting for $label device matching hint '$hint'..."
    sleep "$SATELLITE_WAIT_FOR_DEVICE_SECONDS"
  done
}

if [[ ! -x "$WYOMING_SATELLITE_DIR/script/run" ]]; then
  echo "wyoming-satellite launcher not found at $WYOMING_SATELLITE_DIR/script/run" >&2
  exit 1
fi

MIC_DEVICE="$(wait_for_device arecord "$MIC_DEVICE_HINT" microphone)"
SND_DEVICE="$(find_alsa_device aplay "$SND_DEVICE_HINT")"
if [[ -z "$SND_DEVICE" ]]; then
  SND_DEVICE="default"
  echo "No dedicated playback device matched '$SND_DEVICE_HINT'; falling back to '$SND_DEVICE'"
else
  echo "Detected speaker device: $SND_DEVICE"
fi

DEBUG_ARGS=()
if [[ "$SATELLITE_DEBUG" == "1" ]]; then
  DEBUG_ARGS+=(--debug)
fi

MIC_COMMAND="arecord -D ${MIC_DEVICE} -r 16000 -c 1 -f S16_LE -t raw"
SND_COMMAND="aplay -D ${SND_DEVICE} -r 22050 -c 1 -f S16_LE -t raw"

exec "$WYOMING_SATELLITE_DIR/script/run" \
  --name "$SATELLITE_NAME" \
  --uri "$SATELLITE_URI" \
  --mic-command "$MIC_COMMAND" \
  --snd-command "$SND_COMMAND" \
  --wake-uri "$WAKE_URI" \
  --wake-word-name "$WAKE_WORD_NAME" \
  --mic-auto-gain "$MIC_AUTO_GAIN" \
  --mic-noise-suppression "$MIC_NOISE_SUPPRESSION" \
  --mic-volume-multiplier "$MIC_VOLUME_MULTIPLIER" \
  --snd-volume-multiplier "$SND_VOLUME_MULTIPLIER" \
  "${DEBUG_ARGS[@]}"
