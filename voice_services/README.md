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
- `Speech-to-Phrase` for command-focused local transcription
- `Whisper` only as an opt-in fallback for open-ended transcription tests
- `Piper` for local English text-to-speech

The provided `compose.yaml` now starts:

- `wyoming-openwakeword` on port `10400`
- `wyoming-speech-to-phrase` on port `10300`
- `wyoming-piper` on port `10200`

Whisper remains available behind the `whisper` Compose profile on host port
`10301`, but it is no longer the normal STT path for the Assist pipeline.

Default language/voice settings are:

- STT engine: `Speech-to-Phrase`
- Speech-to-Phrase custom sentences: `/home/lucas/homeassistant-bootstrap/custom_sentences`
- Speech-to-Phrase Home Assistant token/websocket: copied from the bridge `.env`
  by `configure_voice_services_env.sh` during deploy
- Whisper fallback language/model: `en` / `base-int8`
- Piper voice: `en_US-lessac-medium`
- Wake word: `hey_jarvis`
- openWakeWord threshold: `0.18` to reduce false wake/no-speech loops on this ReSpeaker setup,
  especially after long idle periods where the first wake was sometimes missed at `0.17`
- openWakeWord trigger level: `1`
- openWakeWord refractory: `8.0` seconds
- Wake refractory: `8` seconds on the satellite side
- Microphone auto gain: `5`
- Microphone noise suppression: `2`
- Microphone volume multiplier: `1.0`
- Microphone channel index: auto-select from the stereo capture stream
- Microphone mute after wake beep: `0.0` seconds so the command start is not clipped
- Streaming watchdog timeout: `20` seconds so slow but valid local STT turns are not cut off
- No-speech restart timeout: `0` seconds, disabled; the streaming watchdog handles true hangs without restart churn
- Transcript timeout: `0` seconds, disabled by default to avoid killing delayed transcripts
- Satellite debug recording: disabled for normal use to avoid extra I/O and log noise
- Post-transcript self-trigger restart window: `0` seconds, disabled by default; wake refractory handles immediate self-triggers without breaking wake-to-command correlation

## Important note about transcription quality

The microphone can improve audio capture a lot, but transcription quality is not
decided by the microphone alone.

The official Home Assistant local voice stack supports Wyoming-based services such as:

- Whisper
- Speech-to-Phrase
- Piper
- openWakeWord

For this repo, the primary failure mode we observed was not bridge execution. The
bridge executed correctly when it received text like "turn off the studio
lights", but Whisper often emitted unrelated or malformed text such as invented
commands. Speech-to-Phrase is a better fit for the current narrow voice surface:
lights, Mac apps, monitor control, and named routines.

If we need open-ended natural language later, bring Whisper back intentionally as
a secondary pipeline instead of putting it back on the main command path.

The satellite wrapper also waits for the local wake service port to become
reachable before it fully starts. That makes redeploys and reboots calmer when
`openwakeword` is still coming up.

## Current setup steps

The microphone is connected, so the practical setup order is:

1. Start the Wyoming services:

```bash
cd /home/lucas/ha-command-bridge/voice_services
cp wyoming_services.env.example .env
./configure_voice_services_env.sh
docker compose up --build -d
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
docker logs --tail 20 wyoming-speech-to-phrase
docker logs --tail 20 wyoming-piper
systemctl status wyoming-satellite.service --no-pager -l
ss -ltn | egrep '10200|10300|10400|10700'
```

When using the helper scripts from this repo, recent logs are easier to read if
you keep the default time window and only ask for older history when needed:

```bash
./scripts/pi wake-debug
./scripts/pi voice-check
./scripts/pi logs satellite --since 2h
./scripts/pi logs stt --all
./scripts/pi debug-clean --older-than 24h
```

That keeps routine checks focused on fresh events while still letting you keep
older journal history for deep diagnosis.

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

By default this repo does not force-restart the satellite on no-speech turns:

Default:

```bash
SATELLITE_NO_SPEECH_TIMEOUT_SECONDS=0
```

This avoids restart churn when openWakeWord fires but no usable speech follows.
For true stuck streaming states, keep the streaming watchdog enabled instead.

## Transcript timeout after STT stops

If STT ends but the recognizer is still decoding, the satellite can optionally wait for
the transcript and then force a recovery if it never arrives.

Default:

```bash
SATELLITE_TRANSCRIPT_TIMEOUT_SECONDS=0
```

Leave this disabled unless you have a confirmed stuck transcript path.

## Whisper fallback runtime

This repo keeps the local `whisper_patch` image build for `wyoming-whisper`, but
only as a fallback service.

That patch keeps the upstream runtime, but replaces the event handler with a
small hotfix for the known `AudioStop`/`AssertionError` failure mode that can
drop whole wake cycles before any transcript reaches the bridge.

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

1. Keep `SATELLITE_STREAMING_TIMEOUT_SECONDS=20` as the normal project default
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

When you later need a more aggressive watchdog, lower
`SATELLITE_STREAMING_TIMEOUT_SECONDS` below the default `20`.
The satellite wrapper will automatically set hook commands that create a state
file when streaming starts and clear it on `stt_stop`, transcript, stop, or
error. The watchdog timer checks that state file every 10 seconds and restarts
`wyoming-satellite.service` if the stream stays open longer than the configured
timeout.

## Notes for this project
- The user should not need to edit backend `.env` files just to use the microphone.
- The remaining tuning work should mostly be:
  - verifying capture quality
  - final gain/noise suppression tuning
  - checking wake word reliability in the real room
