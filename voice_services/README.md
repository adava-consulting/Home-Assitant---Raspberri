# Voice Satellite Preparation

This folder prepares the Raspberry Pi host to use a `ReSpeaker Lite USB`
microphone as a Wyoming voice satellite for Home Assistant.

For a future Codex session, read this handoff first:

- `voice_services/RESPEAKER_LITE_HANDOFF.md`

## Why this approach

For this project, the clean architecture is:

`ReSpeaker Lite USB -> Wyoming Satellite -> Home Assistant Assist -> backend -> Home Assistant -> devices`

That keeps voice capture separate from the command backend and lets Home Assistant
remain the voice entry point.

## What is already prepared

- `run_wyoming_satellite.sh`
  - waits for the USB microphone to appear
  - auto-detects matching ALSA devices
  - supports configurable capture channel count/channel index for USB mics like ReSpeaker Lite
  - starts Wyoming Satellite with sensible microphone settings
- `respeaker_lite_satellite.env.example`
  - central place for wake word, device hints, and audio tuning
  - can route confirmation/TTS audio to the Raspberry headphone jack with `SND_DEVICE_HINT="Headphones"`
- `wyoming-satellite.service.example`
  - systemd template so the satellite can start automatically on boot

## Recommended voice stack for this project

For hands-free use with an English-speaking user:

- `wyoming-satellite` on the Raspberry Pi host
- `openWakeWord` for the wake word
- `Whisper` for flexible English transcription
- `Piper` for local English text-to-speech

The provided `compose.yaml` now starts:

- `wyoming-openwakeword` on port `10400`
- `wyoming-whisper` on port `10300`
- `wyoming-piper` on port `10200`

Default language/voice settings are:

- Whisper language: `en`
- Whisper model: `base-int8`
- Whisper beam size: `1`
- Whisper CPU threads: `4`
- Whisper initial prompt: biased toward room, studio, and all-lights commands
- Whisper VAD minimum speech: `200 ms`
- Whisper VAD minimum silence: `700 ms`
- Piper voice: `en_US-lessac-medium`
- Wake word: `hey_jarvis`
- openWakeWord threshold: `0.35` to reduce missed activations in this room
- openWakeWord trigger level: `1`
- openWakeWord refractory: `1.0` second
- Wake refractory: `1` second for faster re-trigger after short or empty wake-ups
- Microphone mute after wake beep: `0.0` seconds so the command start is not clipped
- Streaming watchdog timeout: `8` seconds
- No-speech restart timeout: `0` by default to avoid long reconnect gaps after empty wake-ups

## Important note about transcription quality

The microphone can improve audio capture a lot, but transcription quality is not
decided by the microphone alone.

The official Home Assistant local voice stack supports Wyoming-based services such as:

- Whisper
- Speech-to-Phrase
- Piper
- openWakeWord

For open-ended natural language, Speech-to-Phrase is usually not the right fit as
the first implementation step because it needs tighter Home Assistant coupling.
For this repo, the safer first cut is:

- better audio capture from the ReSpeaker Lite
- local wake word detection
- English Whisper for transcription, using at least `base-int8` for better command accuracy
- lower Whisper beam size for faster decoding on Raspberry Pi CPU
- a small initial prompt with likely home commands to reduce weird STT substitutions
- shorter Whisper VAD silence windows so commands close faster after you stop talking
- Piper for local speech output

If tiny Whisper is still inaccurate after the microphone is tuned, the next step
should be a stronger English model, not a return to manual YAML editing.

## Current setup steps

The microphone is connected, so the practical setup order is:

1. Start the Wyoming services:

```bash
cd /home/lucas/ha-command-bridge/voice_services
cp wyoming_services.env.example .env
docker compose up -d
docker compose ps
```

2. Install the official `wyoming-satellite` repository on the Raspberry Pi host.
3. Copy:
   - `respeaker_lite_satellite.env.example` -> `respeaker_lite_satellite.env`
4. Adjust the wake word and device hints if needed.
   - If a device hint contains spaces, keep it quoted in the env file.
5. Copy `wyoming-satellite.service.example` into `/etc/systemd/system/wyoming-satellite.service`
6. Enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now wyoming-satellite.service
```

7. In Home Assistant:
   - add the discovered Wyoming services
   - add the Wyoming satellite
   - create an English Assist pipeline that uses:
     - `openWakeWord`
     - `Whisper`
     - `Piper`
     - Home Assistant conversation agent

## Quick verification

After deployment, these checks should all look healthy:

```bash
cd /home/lucas/ha-command-bridge/voice_services
docker compose ps
docker logs --tail 20 wyoming-openwakeword
docker logs --tail 20 wyoming-whisper
docker logs --tail 20 wyoming-piper
systemctl status wyoming-satellite.service --no-pager -l
ss -ltn | egrep '10200|10300|10400|10700'
```

## Optional listening beep on the headphone jack

If you want a confirmation sound when the satellite starts listening:

1. Set `SND_DEVICE_HINT="Headphones"` in `respeaker_lite_satellite.env`
2. Set `SATELLITE_AWAKE_WAV` to a short WAV file, for example:
3. If the first words of the command get clipped, set `MIC_SECONDS_TO_MUTE_AFTER_AWAKE_WAV=0.0`
   because the beep is going to headphones instead of a speaker in the room.
4. If the beep is still too quiet, configure the Raspberry headphone mixer too.

```bash
SATELLITE_AWAKE_WAV=/home/lucas/ha-command-bridge/voice_services/sounds/listening.wav
MIC_SECONDS_TO_MUTE_AFTER_AWAKE_WAV=0.0
SND_MIXER_CARD=Headphones
SND_MIXER_CONTROL=PCM
SND_MIXER_LEVEL=100%
```

This makes the wake confirmation play through the Raspberry 3.5mm jack instead of
the ReSpeaker's own playback device.

## Satellite event hooks

The wrapper can now fire a local hook on these phases:

- `detection`
- `streaming_start`
- `streaming_stop`
- `transcript`
- `error`

The default hook script still maintains the streaming watchdog state, but it can
also:

- append timestamped events to `SATELLITE_EVENT_LOG_FILE`
- run one shell command per phase through:
  - `SATELLITE_ON_DETECTION_COMMAND`
  - `SATELLITE_ON_STREAMING_START_COMMAND`
  - `SATELLITE_ON_STREAMING_STOP_COMMAND`
  - `SATELLITE_ON_TRANSCRIPT_COMMAND`
  - `SATELLITE_ON_STT_START_COMMAND`
  - `SATELLITE_ON_STT_STOP_COMMAND`
  - `SATELLITE_ON_ERROR_COMMAND`

Example:

```bash
SATELLITE_EVENT_LOG_FILE=/tmp/wyoming-satellite-events.log
SATELLITE_ON_DETECTION_COMMAND='logger -t wyoming-satellite "wake word detected"'
SATELLITE_ON_ERROR_COMMAND='logger -t wyoming-satellite "satellite error"'
```

That gives us a clean place to attach future visual feedback without changing the
satellite launch command again.

## No-speech timeout after wake word

By default this repo keeps the no-speech restart disabled:

Stable default:

```bash
SATELLITE_NO_SPEECH_TIMEOUT_SECONDS=0
```

This avoids long reconnect gaps after an empty wake-up.

If you specifically want aggressive recovery from empty wake-ups, you can enable
a short restart window:

```bash
SATELLITE_NO_SPEECH_TIMEOUT_SECONDS=3
```

With that enabled, the hook script starts a short timer on wake detection. If
the user does not actually begin speaking before the timeout, the satellite
process is terminated and systemd brings it back immediately. If
`HOME_ASSISTANT_URL` and `HOME_ASSISTANT_TOKEN` are available through the
project `.env`, the hook also nudges `assist_satellite.respeaker_lite` back to
`idle` by calling `assist_satellite.announce` with an empty message. This keeps
the Home Assistant entity state closer to the real satellite state during empty
wake-ups.

## ReSpeaker Lite RGB note

In the current project layout, the Raspberry is using the ReSpeaker Lite as a USB
audio device. That path exposes the microphone and speaker, but not an obvious Linux
LED device for the onboard RGB. If we want Alexa-style RGB feedback on the device
itself, the practical next step is to drive that LED from the ReSpeaker/XIAO side
with dedicated firmware or an ESPHome-style integration, and then connect these
satellite event hooks to it.

## Streaming safety watchdog

If the satellite ever gets stuck in `listening` after a wake word, enable the
watchdog that restarts it when streaming stays open too long.

1. Keep `SATELLITE_STREAMING_TIMEOUT_SECONDS=8` in `respeaker_lite_satellite.env`
2. Install the helper scripts and both systemd units:
   - `satellite_watchdog_hook.sh`
   - `satellite_watchdog_check.sh`
   - `wyoming-satellite-watchdog.service`
   - `wyoming-satellite-watchdog.timer`
3. Enable the timer:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now wyoming-satellite-watchdog.timer
```

The satellite wrapper will automatically set hook commands that create a state
file when streaming starts and clear it on transcript, stop, or error. The
watchdog timer checks that state file every 10 seconds and restarts
`wyoming-satellite.service` if the stream stays open longer than the configured
timeout.

## Notes for this project
- The user should not need to edit backend `.env` files just to use the microphone.
- The remaining tuning work should mostly be:
  - verifying capture quality
  - final gain/noise suppression tuning
  - checking wake word reliability in the real room
