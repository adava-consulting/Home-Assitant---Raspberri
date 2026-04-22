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
- Whisper beam size: `3`
- Whisper CPU threads: `4`
- Whisper initial prompt: conservative home-automation guidance that tells the model to prefer no text over guessing and keeps only a short list of common room/studio commands for context
- Whisper VAD filter: disabled by default in this project because low capture
  levels on the ReSpeaker were causing full commands to be discarded as silence
- Piper voice: `en_US-lessac-medium`
- Wake word: `hey_jarvis`
- openWakeWord threshold: `0.15` to favor first-try activations on this ReSpeaker setup,
  especially after long idle periods where the first wake was sometimes missed at `0.17`
- openWakeWord trigger level: `1`
- openWakeWord refractory: `8.0` seconds
- Wake refractory: `8` seconds on the satellite side
- Microphone auto gain: `15`
- Microphone noise suppression: `0`
- Microphone volume multiplier: `4.0`
- Microphone channel index: auto-select from the stereo capture stream
- Microphone mute after wake beep: `0.0` seconds so the command start is not clipped
- Streaming watchdog timeout: `8` seconds
- No-speech restart timeout: `7` seconds so the first spoken words are less likely to be missed
- Transcript timeout: `12` seconds so Whisper has enough time to finish short commands before the watchdog forces a restart
- Post-transcript self-trigger restart window: `2` seconds so we only reset on the immediate false second wake, not on a real follow-up request a few seconds later

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
- a slightly wider Whisper beam to better separate short `on/off` commands on Raspberry Pi CPU
- a small, conservative initial prompt that helps with common commands without encouraging aggressive guessing
- no Whisper VAD filtering by default because the ReSpeaker capture in this room
  was quiet enough that VAD sometimes discarded the full spoken command
- Piper for local speech output

If `base-int8` is still inaccurate after the microphone is tuned, the next step
should be a stronger English model, not a return to manual YAML editing.

The satellite wrapper also waits for the local wake service port to become
reachable before it fully starts. That makes redeploys and reboots calmer when
`openwakeword` is still coming up.

## Current setup steps

The microphone is connected, so the practical setup order is:

1. Start the Wyoming services:

```bash
cd /home/lucas/ha-command-bridge/voice_services
cp wyoming_services.env.example .env
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
docker logs --tail 20 wyoming-whisper
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
./scripts/pi logs whisper --all
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

By default this repo uses a short no-speech restart:

Stable default:

```bash
SATELLITE_NO_SPEECH_TIMEOUT_SECONDS=7
```

This helps the satellite recover quickly after a false wake that never turns
into real speech.

## Transcript timeout after STT stops

If STT ends but Whisper is still decoding, the satellite should wait long
enough for the transcript to come back before forcing a recovery.

Stable default:

```bash
SATELLITE_TRANSCRIPT_TIMEOUT_SECONDS=12
```

This gives short home-automation commands enough time to finish decoding on the
Pi without leaving the satellite stuck for too long when Whisper actually hangs.

If you want to disable that behavior entirely:

```bash
SATELLITE_TRANSCRIPT_TIMEOUT_SECONDS=0
```

With that enabled, the hook script starts a short timer on wake detection. If
the user does not actually begin speaking before the timeout, the satellite

## Local Whisper hotfix

This repo intentionally builds the `whisper` service from
[`voice_services/whisper_patch`](./whisper_patch) instead of using the upstream
image directly.

Reason: upstream `rhasspy/wyoming-faster-whisper` currently has an open issue
where `AudioStop` can arrive without prior `AudioChunk`, causing an
`AssertionError` in `dispatch_handler.py` and making later wake cycles
unreliable. The local patch returns an empty transcript instead of crashing so
the service stays healthy across repeated wake attempts.
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

1. Keep `SATELLITE_STREAMING_TIMEOUT_SECONDS=8` as the normal project default
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
`SATELLITE_STREAMING_TIMEOUT_SECONDS` below the default `8`.
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
