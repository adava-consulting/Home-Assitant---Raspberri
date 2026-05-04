# Safe Restore Checkpoint

Checkpoint date: 2026-05-04

Safe tag for this snapshot: `safe-voice-checkpoint-2026-05-04`

Remote repository:

```bash
https://github.com/adava-consulting/Home-Assitant---Raspberri.git
```

## Purpose

This document captures the project state before the next round of voice-stack
changes. It is meant to make the current repository reproducible from Git if we
need to return to this baseline with `pull`, `checkout`, or a fresh clone.

This checkpoint intentionally does not commit secrets, local credentials, debug
bundles, virtual environments, or generated model data.

## Restore From Git

Fresh clone:

```bash
git clone https://github.com/adava-consulting/Home-Assitant---Raspberri.git HomeAssistant
cd HomeAssistant
git fetch --tags origin
git checkout safe-voice-checkpoint-2026-05-04
```

Existing checkout:

```bash
cd /Users/marcos/Documents/HomeAssistant
git fetch --tags origin
git checkout main
git pull --ff-only origin main
```

To inspect the exact safe snapshot without moving `main`:

```bash
git checkout safe-voice-checkpoint-2026-05-04
```

To move local `main` back to the safe snapshot, only if you do not need local
uncommitted changes:

```bash
git checkout main
git reset --hard safe-voice-checkpoint-2026-05-04
```

## Private Files To Recreate

These files are required locally but are intentionally ignored by Git:

- `.env`: bridge runtime settings and Home Assistant token.
- `.pi.env`: Raspberry Pi SSH/deploy helper settings.
- `credentials.env`: legacy/local credential helper if used.
- `voice_services/.env`: voice service runtime env, generated during deploy.
- `voice_services/respeaker_lite_satellite.env`: Raspberry satellite runtime env.

Start from the tracked examples:

```bash
cp .env.example .env
cp .pi.env.example .pi.env
cp voice_services/wyoming_services.env.example voice_services/.env
cp voice_services/respeaker_lite_satellite.env.example voice_services/respeaker_lite_satellite.env
```

Minimum local `.pi.env` values:

```bash
PI_HOST=192.168.0.141
PI_USER=lucas
PI_PASSWORD=<raspberry-password>
PI_REMOTE_BRIDGE_DIR=/home/lucas/ha-command-bridge
PI_REMOTE_BOOTSTRAP_DIR=/home/lucas/homeassistant-bootstrap
```

Minimum bridge `.env` values:

```bash
HOME_ASSISTANT_URL=http://host.docker.internal:8123
HOME_ASSISTANT_TOKEN=<home-assistant-long-lived-token>
VOICE_MODEL_FILE=/home/claude-host-home/ha-command-bridge/voice_model.json
FAST_PATH_LOCAL_FIRST=1
AUTO_DISCOVER_ENTITIES=0
AUTO_DISCOVER_INCLUDE_UNAVAILABLE=0
AUDIO_RESPONSE_ENABLED=0
ASSIST_GUARD_ENABLED=1
ASSIST_GUARD_RECENT_WAKE_WINDOW_SECONDS=45
MAC_CONTROL_ENABLED=1
MAC_CONTROL_SSH_HOST=192.168.0.29
MAC_CONTROL_SSH_USER=marcos
MAC_CONTROL_REMOTE_SCRIPT_PATH=/Users/marcos/ha-command-bridge/mac_tools/mac_control.sh
```

`voice_services/configure_voice_services_env.sh` copies the Home Assistant
connection from `.env` into `voice_services/.env` during deploy, including the
Speech-to-Phrase websocket URL and token.

## Current Voice Architecture

Normal voice path:

```text
ReSpeaker Lite USB
-> wyoming-satellite systemd service on Raspberry Pi host
-> openWakeWord on port 10400
-> Speech-to-Phrase on port 10300
-> Home Assistant Assist
-> ha-command-bridge on port 8000
-> Home Assistant services / Mac control scripts
```

Important services:

- `wyoming-openwakeword`: default wake service, port `10400`.
- `wyoming-speech-to-phrase`: primary STT, port `10300`.
- `wyoming-whisper`: fallback STT only, Compose profile `whisper`, host port `10301`.
- `wyoming-piper`: TTS, port `10200`.
- `wyoming-satellite.service`: host systemd service, satellite port `10700`.
- `ha-command-bridge`: bridge API, port `8000`.
- `homeassistant`: Home Assistant container.

Important voice settings:

```bash
STT_ENGINE=speech_to_phrase
WAKE_WORD_MODEL=hey_jarvis
WAKE_WORD_THRESHOLD=0.18
WAKE_WORD_TRIGGER_LEVEL=1
WAKE_WORD_REFRACTORY_SECONDS=8.0
WAKE_REFRACTORY_SECONDS=8
SATELLITE_STREAMING_TIMEOUT_SECONDS=20
SATELLITE_NO_SPEECH_TIMEOUT_SECONDS=0
SATELLITE_TRANSCRIPT_TIMEOUT_SECONDS=0
SATELLITE_POST_TRANSCRIPT_COOLDOWN_SECONDS=0
MIC_SECONDS_TO_MUTE_AFTER_AWAKE_WAV=0.0
SND_VOLUME_MULTIPLIER=2.5
```

## Deploy To Raspberry Pi

From the Mac checkout:

```bash
./scripts/pi redeploy
```

That command syncs:

- bridge app and Docker image
- `voice_model.json`
- `voice_services/`
- Home Assistant bootstrap YAML
- monitor control helpers
- systemd satellite restart

For voice-only redeploy:

```bash
./scripts/pi deploy-voice
```

For Home Assistant YAML only:

```bash
./scripts/pi sync-ha
```

## Verify After Restore Or Pull

Run:

```bash
./scripts/pi voice-check
./scripts/pi wake-debug --since 10m
./scripts/pi latency-check --since 10m
./scripts/pi logs stt --since 10m
./scripts/pi logs satellite --since 10m
```

Expected signs of the checkpoint state:

- `STT_ENGINE=speech_to_phrase` appears in voice service env output.
- `wyoming-speech-to-phrase` is running and owns host port `10300`.
- `wyoming-whisper` is not on port `10300`; if enabled manually, it uses host port `10301`.
- `SATELLITE_NO_SPEECH_TIMEOUT_SECONDS=0`.
- `VOICE_MODEL_FILE=/home/claude-host-home/ha-command-bridge/voice_model.json`.
- `FAST_PATH_LOCAL_FIRST=1`.

## Local Validation Commands

Before this checkpoint was prepared, these validations passed locally:

```bash
./.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
bash -n scripts/pi scripts/redeploy_pi_stack.sh voice_services/configure_voice_services_env.sh
./.venv/bin/python -c "import yaml, pathlib; [yaml.safe_load(pathlib.Path(p).read_text()) for p in ['voice_services/compose.yaml','homeassistant_bootstrap/custom_sentences/en/fast_commands.yaml','homeassistant_bootstrap/intent_scripts.yaml']]"
git diff --check
```

Last observed full test result:

```text
Ran 187 tests in 12.572s
OK
```

## Known Runtime Caveat

This checkpoint captures the current source baseline. The voice behavior was
still under active validation, and the next work item is to use fresh Raspberry
logs to determine whether Speech-to-Phrase training/routing, wake handling, or
audio capture still needs adjustment.

Do not treat this as a final polished voice release. Treat it as the safe source
checkpoint before further experiments.
