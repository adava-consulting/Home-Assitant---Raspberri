# Voice Satellite Preparation

This folder prepares the Raspberry Pi host to use a future `ReSpeaker Lite USB`
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
  - starts Wyoming Satellite with sensible microphone settings
- `respeaker_lite_satellite.env.example`
  - central place for wake word, device hints, and audio tuning
- `wyoming-satellite.service.example`
  - systemd template so the satellite can start automatically on boot

## Recommended voice stack

For hands-free use:

- `wyoming-satellite` on the Raspberry Pi host
- `openWakeWord` for the wake word
- a speech-to-text provider chosen based on quality/speed tradeoff

## Important note about transcription quality

The microphone can improve audio capture a lot, but transcription quality is not
decided by the microphone alone.

The official Home Assistant local voice stack supports Wyoming-based services such as:

- Whisper
- Speech-to-Phrase
- Piper
- openWakeWord

For open-ended natural language, Speech-to-Phrase is usually not the right fit.
If tiny Whisper was inaccurate before, the main improvement will likely come from:

- better audio capture from the ReSpeaker Lite
- Wyoming satellite noise suppression / auto gain tuning
- a stronger speech-to-text backend than a tiny local model

## When the microphone arrives

1. Install the official `wyoming-satellite` repository on the Raspberry Pi host.
2. Copy:
   - `respeaker_lite_satellite.env.example` -> `respeaker_lite_satellite.env`
3. Adjust the wake word and device hints if needed.
4. Copy `wyoming-satellite.service.example` into `/etc/systemd/system/wyoming-satellite.service`
5. Enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now wyoming-satellite.service
```

6. In Home Assistant:
   - add the Wyoming satellite if it is discovered
   - or add it manually
   - then select the satellite in your voice assistant pipeline

## Notes for this project

- The user should not need to edit backend `.env` files just to use the microphone.
- The remaining work, once hardware exists, should mostly be:
  - installing `wyoming-satellite`
  - verifying the ALSA device name
  - final tuning for gain/noise suppression/wake word
