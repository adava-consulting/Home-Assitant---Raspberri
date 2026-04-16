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
: "${WAKE_WORD_NAME:=hey_jarvis}"
: "${WAKE_REFRACTORY_SECONDS:=2}"
: "${MIC_DEVICE_HINT:=ReSpeaker Lite}"
: "${SND_DEVICE_HINT:=ReSpeaker Lite}"
: "${MIC_AUTO_GAIN:=5}"
: "${MIC_NOISE_SUPPRESSION:=2}"
: "${MIC_VOLUME_MULTIPLIER:=1.0}"
: "${MIC_CAPTURE_CHANNELS:=2}"
: "${MIC_CHANNEL_INDEX:=}"
: "${MIC_SECONDS_TO_MUTE_AFTER_AWAKE_WAV:=0.0}"
: "${SND_VOLUME_MULTIPLIER:=1.0}"
: "${SND_MIXER_CARD:=}"
: "${SND_MIXER_CONTROL:=}"
: "${SND_MIXER_LEVEL:=}"
: "${SATELLITE_AWAKE_WAV:=}"
: "${SATELLITE_DONE_WAV:=}"
: "${SATELLITE_TIMER_FINISHED_WAV:=}"
: "${SATELLITE_DEBUG_RECORDING_DIR:=}"
: "${SATELLITE_STREAMING_TIMEOUT_SECONDS:=8}"
: "${SATELLITE_WATCHDOG_STATE_FILE:=/tmp/wyoming-satellite-watchdog.state}"
: "${SATELLITE_NO_SPEECH_TIMEOUT_SECONDS:=0}"
: "${SATELLITE_NO_SPEECH_STATE_FILE:=/tmp/wyoming-satellite-no-speech.state}"
: "${SATELLITE_WAIT_FOR_DEVICE_SECONDS:=5}"
: "${SATELLITE_DEBUG:=0}"

find_alsa_device() {
  local tool="$1"
  local hint="$2"

  "$tool" -L 2>/dev/null | python3 -c '
import sys

hint = sys.argv[1].strip().lower()
entries = []
current_name = None
current_desc = []

for raw_line in sys.stdin:
    line = raw_line.rstrip("\n")
    if not line.strip():
        continue

    if line[:1].isspace():
        if current_name is not None:
            current_desc.append(line.strip())
        continue

    if current_name is not None:
        entries.append((current_name, " ".join(current_desc)))

    current_name = line.strip()
    current_desc = []

if current_name is not None:
    entries.append((current_name, " ".join(current_desc)))

matches = []
for name, desc in entries:
    haystack = f"{name} {desc}".lower()
    if hint in haystack:
        matches.append(name)

preferred = [name for name in matches if name.startswith("plughw:")]
selected = preferred[0] if preferred else (matches[0] if matches else "")
print(selected)
' "$hint"
}

wait_for_device() {
  local tool="$1"
  local hint="$2"
  local label="$3"

  local device=""
  while [[ -z "$device" ]]; do
    device="$(find_alsa_device "$tool" "$hint")"
    if [[ -n "$device" ]]; then
      echo "Detected $label device: $device" >&2
      printf '%s\n' "$device"
      return 0
    fi

    echo "Waiting for $label device matching hint '$hint'..." >&2
    sleep "$SATELLITE_WAIT_FOR_DEVICE_SECONDS"
  done
}

configure_playback_mixer() {
  if [[ -z "$SND_MIXER_CARD" ]] || [[ -z "$SND_MIXER_CONTROL" ]] || [[ -z "$SND_MIXER_LEVEL" ]]; then
    return 0
  fi

  if ! command -v amixer >/dev/null 2>&1; then
    echo "amixer not found; skipping playback mixer configuration" >&2
    return 0
  fi

  if amixer -c "$SND_MIXER_CARD" sset "$SND_MIXER_CONTROL" "$SND_MIXER_LEVEL" unmute >/dev/null 2>&1; then
    echo "Configured playback mixer: card=$SND_MIXER_CARD control=$SND_MIXER_CONTROL level=$SND_MIXER_LEVEL"
  else
    echo "Failed to configure playback mixer: card=$SND_MIXER_CARD control=$SND_MIXER_CONTROL level=$SND_MIXER_LEVEL" >&2
  fi
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

configure_playback_mixer

DEBUG_ARGS=()
if [[ "$SATELLITE_DEBUG" == "1" ]]; then
  DEBUG_ARGS+=(--debug)
fi

OPTIONAL_ARGS=()
WATCHDOG_HOOK_SCRIPT="$SCRIPT_DIR/satellite_watchdog_hook.sh"
if [[ -x "$WATCHDOG_HOOK_SCRIPT" ]]; then
  OPTIONAL_ARGS+=(--streaming-start-command "$WATCHDOG_HOOK_SCRIPT streaming_start")
  OPTIONAL_ARGS+=(--streaming-stop-command "$WATCHDOG_HOOK_SCRIPT streaming_stop")
  OPTIONAL_ARGS+=(--detection-command "$WATCHDOG_HOOK_SCRIPT detection")
  OPTIONAL_ARGS+=(--transcript-command "$WATCHDOG_HOOK_SCRIPT transcript")
  OPTIONAL_ARGS+=(--stt-start-command "$WATCHDOG_HOOK_SCRIPT stt_start")
  OPTIONAL_ARGS+=(--stt-stop-command "$WATCHDOG_HOOK_SCRIPT stt_stop")
  OPTIONAL_ARGS+=(--error-command "$WATCHDOG_HOOK_SCRIPT error")
fi
if [[ "${SATELLITE_STREAMING_TIMEOUT_SECONDS}" != "0" ]]; then
  rm -f "$SATELLITE_WATCHDOG_STATE_FILE"
fi
if [[ "${SATELLITE_NO_SPEECH_TIMEOUT_SECONDS}" != "0" ]]; then
  rm -f "$SATELLITE_NO_SPEECH_STATE_FILE"
fi
if [[ -n "$WAKE_URI" ]]; then
  OPTIONAL_ARGS+=(--wake-uri "$WAKE_URI")
fi
if [[ -n "$WAKE_WORD_NAME" ]]; then
  OPTIONAL_ARGS+=(--wake-word-name "$WAKE_WORD_NAME")
  OPTIONAL_ARGS+=(--wake-refractory-seconds "$WAKE_REFRACTORY_SECONDS")
fi
if [[ -n "$SATELLITE_AWAKE_WAV" ]]; then
  OPTIONAL_ARGS+=(--awake-wav "$SATELLITE_AWAKE_WAV")
fi
if [[ -n "$SATELLITE_DONE_WAV" ]]; then
  OPTIONAL_ARGS+=(--done-wav "$SATELLITE_DONE_WAV")
fi
if [[ -n "$SATELLITE_TIMER_FINISHED_WAV" ]]; then
  OPTIONAL_ARGS+=(--timer-finished-wav "$SATELLITE_TIMER_FINISHED_WAV")
fi
if [[ -n "$SATELLITE_DEBUG_RECORDING_DIR" ]]; then
  OPTIONAL_ARGS+=(--debug-recording-dir "$SATELLITE_DEBUG_RECORDING_DIR")
fi

MIC_COMMAND="arecord -D ${MIC_DEVICE} -r 16000 -c ${MIC_CAPTURE_CHANNELS} -f S16_LE -t raw"
SND_COMMAND="aplay -D ${SND_DEVICE} -r 22050 -c 1 -f S16_LE -t raw"

MIC_OPTIONAL_ARGS=()
if [[ -n "$MIC_CHANNEL_INDEX" ]]; then
  MIC_OPTIONAL_ARGS+=(--mic-channel-index "$MIC_CHANNEL_INDEX")
fi

exec "$WYOMING_SATELLITE_DIR/script/run" \
  --name "$SATELLITE_NAME" \
  --uri "$SATELLITE_URI" \
  --mic-command "$MIC_COMMAND" \
  --mic-command-channels "$MIC_CAPTURE_CHANNELS" \
  --snd-command "$SND_COMMAND" \
  --mic-auto-gain "$MIC_AUTO_GAIN" \
  --mic-noise-suppression "$MIC_NOISE_SUPPRESSION" \
  --mic-volume-multiplier "$MIC_VOLUME_MULTIPLIER" \
  --mic-seconds-to-mute-after-awake-wav "$MIC_SECONDS_TO_MUTE_AFTER_AWAKE_WAV" \
  --snd-volume-multiplier "$SND_VOLUME_MULTIPLIER" \
  "${MIC_OPTIONAL_ARGS[@]}" \
  "${OPTIONAL_ARGS[@]}" \
  "${DEBUG_ARGS[@]}"
