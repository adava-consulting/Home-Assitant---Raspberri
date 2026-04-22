# ReSpeaker Lite USB Handoff

This document is the handoff note for the day the `ReSpeaker Lite USB` microphone
arrives and gets connected to the Raspberry Pi.

It is written so a future Codex session can read this file first and recover the
right context quickly.

## Project context

- Home Assistant runs on the Raspberry Pi in Docker.
- The command bridge also runs on the Raspberry Pi in Docker.
- The bridge currently uses:
  - `claude_cli` as the default interpreter
  - automatic fallback to `local_rules`
- Device discovery for supported Home Assistant entities is now automatic.
- Complex natural-language commands go through:
  - voice/text input
  - Home Assistant Assist
  - command bridge
  - Claude
  - Home Assistant
  - devices

## Desired voice architecture

The microphone should **not** talk directly to the backend.

The correct flow for this project is:

`ReSpeaker Lite USB -> Wyoming Satellite -> Home Assistant Assist -> command bridge -> Home Assistant -> devices`

Why:

- Home Assistant should remain the voice entry point.
- The backend should remain the smart intent layer.
- This keeps the system easier to maintain and safer to validate.

## Main goal

When the microphone is connected, the user should be able to speak naturally
without using the phone, and the Raspberry Pi should:

1. capture voice from the USB microphone
2. forward it through a Wyoming satellite
3. let Home Assistant transcribe it
4. let the backend interpret it
5. execute the correct actions

## Important expectations

The microphone can improve usability and audio quality a lot, but it does **not**
guarantee perfect transcription by itself.

Transcription quality depends on:

- microphone quality and distance
- gain / noise suppression tuning
- wake word setup
- the speech-to-text engine used by Home Assistant

The previous problem with phone + Whisper was probably not caused by the phone
alone. The full chain matters.

## Current recommendation

Use the ReSpeaker Lite USB as a **Wyoming satellite** running on the Raspberry Pi host.

Keep Home Assistant as the voice controller and the command bridge as the intent layer.

## What is already prepared in this repo

These files already exist to support the future microphone:

- `voice_services/run_wyoming_satellite.sh`
- `voice_services/respeaker_lite_satellite.env.example`
- `voice_services/wyoming-satellite.service.example`
- `voice_services/README.md`

What they do:

- auto-detect the ALSA microphone/speaker device using a device-name hint
- wait for the USB device to appear
- start `wyoming-satellite` with sensible defaults
- provide a `systemd` template to auto-start on boot

## What is still missing

These parts cannot be finalized until the physical microphone is plugged in:

- confirm the real ALSA input device name
- confirm the real ALSA output device name
- test raw recording quality
- tune noise suppression / gain / mic multiplier
- validate wake word behavior
- validate the final speech-to-text choice

## Speech-to-text guidance

For this project, there are two different needs:

1. **Fast closed commands**
- turning lights on/off
- grouped room commands
- simple device commands

2. **Open-ended natural language**
- “I have visitors in my office, make the lights more comfortable”
- “dim the hall but keep the office brighter”

Because of that:

- `Speech-to-Phrase` is usually better for speed on narrow command sets
- `Whisper` is more flexible for open-ended language

The microphone alone will not solve poor open-ended transcription if the STT model
is too weak.

Current repo defaults that matter most for reliability:

- `WAKE_WORD_THRESHOLD=0.15`
- `WHISPER_BEAM_SIZE=3`
- `WHISPER_INITIAL_PROMPT` now explicitly prefers no text over guessing and keeps only a short list of common room/studio commands
- `WAKE_WORD_REFRACTORY_SECONDS=8.0`
- `WAKE_REFRACTORY_SECONDS=8`
- `SATELLITE_NO_SPEECH_TIMEOUT_SECONDS=7`
- `SATELLITE_TRANSCRIPT_TIMEOUT_SECONDS=12`
- `SATELLITE_POST_TRANSCRIPT_COOLDOWN_SECONDS=2`
- `SND_VOLUME_MULTIPLIER=2.5`

Recommended approach:

- start with the better microphone
- keep Home Assistant voice pipeline modular
- test the existing STT with the new mic first
- if transcription is still weak, upgrade the STT layer rather than blaming the mic

## Why this should feel faster than phone Assist

Compared to phone Assist, the ReSpeaker route should improve:

- convenience
- hands-free usage
- less friction to start a request
- more stable microphone placement

It may still not feel “instant” because total latency also depends on:

- speech-to-text
- Claude CLI time
- Home Assistant execution
- Tuya cloud latency for Tuya devices

## The day the microphone arrives

Follow this order.

### 1. Physically connect the device

Plug the ReSpeaker Lite USB into the Raspberry Pi.

### 2. Verify Linux sees it

Run on the Raspberry Pi host:

```bash
arecord -L
aplay -L
lsusb
```

Look for a device name that matches something like `ReSpeaker Lite`.

### 3. Test raw audio capture

Once the device is visible, try:

```bash
arecord -D <MIC_DEVICE> -r 16000 -c 1 -f S16_LE -d 5 /tmp/test.wav
aplay /tmp/test.wav
```

Do this before touching Home Assistant. If raw capture is bad, the satellite will
also be bad.

### 4. Install wyoming-satellite on the Raspberry Pi host

The wrapper in this repo assumes the official `wyoming-satellite` repository exists at:

```text
/opt/wyoming-satellite
```

If it is not installed yet, install it there.

### 5. Create the real env file

Copy:

```bash
cp /home/lucas/ha-command-bridge/voice_services/respeaker_lite_satellite.env.example \
   /home/lucas/ha-command-bridge/voice_services/respeaker_lite_satellite.env
```

Then edit:

- `MIC_DEVICE_HINT`
- `SND_DEVICE_HINT`
- `WAKE_URI`
- `WAKE_WORD_NAME`
- tuning values if needed

### 6. Start the satellite manually first

Run:

```bash
cd /home/lucas/ha-command-bridge/voice_services
chmod +x run_wyoming_satellite.sh
./run_wyoming_satellite.sh
```

This is the safest first test because it gives immediate logs.

### 7. Add the satellite to Home Assistant

In Home Assistant:

- go to `Settings -> Devices & services`
- add or confirm the Wyoming satellite
- assign it to the desired voice assistant pipeline

### 8. Only after manual success, install the systemd service

Copy the template service:

```bash
sudo cp /home/lucas/ha-command-bridge/voice_services/wyoming-satellite.service.example \
        /etc/systemd/system/wyoming-satellite.service
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now wyoming-satellite.service
sudo systemctl status wyoming-satellite.service
```

The goal is that voice satellite startup becomes automatic on boot.

## Audio tuning guidance

Start from the known-good settings that recovered this room after repeated
missed wakes and "no text recognized" failures.

Recommended first-pass values:

- `MIC_AUTO_GAIN=15`
- `MIC_NOISE_SUPPRESSION=0`
- `MIC_VOLUME_MULTIPLIER=4.0`
- `SND_VOLUME_MULTIPLIER=2.5`
- `MIC_CHANNEL_INDEX=` (leave blank so the satellite auto-selects the best channel)
- `WAKE_WORD_THRESHOLD=0.15`
- `WHISPER_BEAM_SIZE=3`
- `WAKE_WORD_TRIGGER_LEVEL=1`
- `WAKE_WORD_REFRACTORY_SECONDS=8.0`
- `WAKE_REFRACTORY_SECONDS=8`
- `SATELLITE_NO_SPEECH_TIMEOUT_SECONDS=7`
- `SATELLITE_POST_TRANSCRIPT_COOLDOWN_SECONDS=2`

If the transcription is too quiet:

- slightly increase `MIC_VOLUME_MULTIPLIER`

If the audio is distorted:

- lower `MIC_VOLUME_MULTIPLIER`
- lower gain

If the room is noisy:

- increase noise suppression carefully, but note that in this setup
  `MIC_NOISE_SUPPRESSION=2` plus Whisper VAD caused full commands to be dropped

## What future Codex should check first

When resuming this task later, check these in order:

1. Is the USB device visible in `arecord -L` and `aplay -L`?
2. Is raw recording intelligible?
3. Does `run_wyoming_satellite.sh` detect the device correctly?
4. Does Home Assistant discover the satellite?
5. Which STT engine is active?
6. Is the latency acceptable?
7. Is the wake word reliable?

## What should not need changing

These things should ideally stay unchanged:

- the backend architecture
- the command bridge request flow
- the user-facing device-discovery mechanism
- the auto-discovery of supported Home Assistant entities

The microphone should improve the input path, not force a redesign of the backend.

## Likely future improvements

After the microphone is working, likely next improvements are:

- choose a final STT engine
- improve wake word reliability
- reduce end-to-end latency
- decide whether simple voice commands should bypass Claude for speed

## Useful official references

- Home Assistant Wyoming integration:
  - https://www.home-assistant.io/integrations/wyoming
- Home Assistant local voice setup:
  - https://www.home-assistant.io/voice_control/voice_remote_local_assistant/
- Wyoming Satellite repository:
  - https://github.com/rhasspy/wyoming-satellite

## Final note

The best practical target is:

- phone no longer required
- wake word works
- Home Assistant transcribes reliably
- backend still handles smart intent decisions
- device execution remains safe and validated
