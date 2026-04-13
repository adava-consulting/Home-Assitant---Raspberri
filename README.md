# Home Assistant Command Bridge

Local Raspberry Pi bridge that receives natural-language requests, interprets them with either local rules, Claude Code CLI, or the Anthropic API, validates the resulting intent, and executes only allowed actions in Home Assistant.

## Goal

This service implements the "intelligent" layer of the project:

- Home Assistant remains the main smart-home orchestrator.
- The command bridge adds a controlled API for natural-language requests.
- A model or local rule engine proposes a structured intent.
- The backend validates every action, target, and parameter before execution.

## Request Flow

1. A client sends a text request such as `get the house ready for bed`.
2. The service reads relevant Home Assistant state.
3. Claude or the local interpreter returns a JSON intent.
4. The backend validates the action, target, and parameters.
5. If valid, the backend calls the mapped Home Assistant service.
6. The API returns the result with traceability.

## Spoken Responses

The bridge can also speak the same `assistant_response` it returns in JSON.

Current implementation:

- the backend returns a short user-facing `assistant_response`
- Home Assistant Assist shows that text back to the user
- when `AUDIO_RESPONSE_ENABLED=1`, the backend also queues the same response for local playback on the Raspberry Pi audio output
- playback uses ALSA through `aplay` / `mpg123` and can synthesize speech with:
  - Kokoro for the preferred free/local higher-quality voice
  - ElevenLabs for a premium cloud voice when credits are available
  - `piper` as the local reliability fallback
  - `espeak-ng` as the simple built-in fallback

Recommended Raspberry Pi analog-jack settings:

- `AUDIO_RESPONSE_ENABLED=1`
- `AUDIO_RESPONSE_ENGINE=kokoro`
- `AUDIO_RESPONSE_DEVICE=plughw:0,0`
- `AUDIO_RESPONSE_CACHE_ENABLED=1`
- `AUDIO_RESPONSE_CACHE_DIR=/home/claude-host-home/ha-command-bridge-data/audio-cache`
- `AUDIO_RESPONSE_FAST_ACK_FOR_LOCAL=0`
- `AUDIO_RESPONSE_FAST_ACK_TEXT=Done.`
- `KOKORO_MODEL_PATH=/home/claude-host-home/ha-command-bridge-data/kokoro/kokoro-v1.0.int8.onnx`
- `KOKORO_VOICES_PATH=/home/claude-host-home/ha-command-bridge-data/kokoro/voices-v1.0.bin`
- `KOKORO_VOICE=af_heart`
- `KOKORO_LANG=en-us`
- `KOKORO_SPEED=1.0`
- `KOKORO_SAMPLE_RATE=24000`
- `KOKORO_WARMUP_ENABLED=1`
- `ELEVENLABS_API_KEY=...`
- `ELEVENLABS_VOICE_ID=pNInz6obpgDQGcFmaJgB`
- `ELEVENLABS_MODEL_ID=eleven_flash_v2_5`
- `ELEVENLABS_OUTPUT_FORMAT=mp3_22050_32`
- `AUDIO_RESPONSE_VOICE=en-us`
- `AUDIO_RESPONSE_SPEED=155`
- `PIPER_COMMAND=piper`
- `PIPER_MODEL_PATH=/home/claude-host-home/ha-command-bridge-data/piper/en_US-lessac-medium.onnx`
- `PIPER_SPEAKER=0`
- `PIPER_LENGTH_SCALE=1.0`

With `AUDIO_RESPONSE_ENGINE=kokoro`, the bridge uses Kokoro first and falls back locally if Kokoro is unavailable. With `AUDIO_RESPONSE_ENGINE=auto`, the bridge prefers Kokoro, then ElevenLabs when an API key is configured, then Piper, and finally `espeak-ng`.
When `KOKORO_WARMUP_ENABLED=1`, the backend loads Kokoro and runs one discarded inference in the background after startup so the first spoken response is faster.
When `AUDIO_RESPONSE_CACHE_ENABLED=1`, repeated Kokoro responses are reused from disk instead of being synthesized again.
When `AUDIO_RESPONSE_FAST_ACK_FOR_LOCAL=0`, Kokoro speaks the same detailed response that Assist receives. Set it to `1` only if you prefer a faster short acknowledgement for simple local commands.

Recommended free/local English voice on the Raspberry Pi:
- Kokoro voice IDs to try first: `af_heart`, `bm_george`, `am_michael`
- Kokoro ONNX files should live under `/home/lucas/ha-command-bridge-data/kokoro/`

Recommended Piper fallback voice:
- `en_US-lessac-medium`
- store both `.onnx` and `.onnx.json` under `/home/lucas/ha-command-bridge-data/piper/` on the host
- the model survives container rebuilds because it lives outside the image

Recommended cloud voice starting point:
- ElevenLabs with `ELEVENLABS_MODEL_ID=eleven_flash_v2_5` for low-latency smart-home confirmations
- default `ELEVENLABS_VOICE_ID=pNInz6obpgDQGcFmaJgB` uses the official Adam example voice from ElevenLabs docs

Design note:

- spoken playback runs in a background queue so API requests do not wait for the audio to finish
- this is meant as the simple local-confirmation path for now
- later the same `assistant_response` text can still be reused with a higher-quality TTS engine if desired

## Scheduling

The bridge supports delayed and absolute execution plans.

Examples:

- `turn on the office lights in 5 minutes`
- `turn off the hall lights in 30 seconds`

Scheduling flow:

1. Claude or the local interpreter returns the action plan and an optional `schedule`.
2. The backend validates the plan immediately.
3. The scheduler persists the job locally on the Raspberry Pi.
4. The API responds right away instead of holding the request open.
5. The scheduler executes the stored plan later when it becomes due.

Current scheduling endpoints:

- `GET /scheduled-jobs`
- `GET /scheduled-jobs?status=pending`
- `POST /scheduled-jobs/{job_id}/cancel`

The scheduler persists jobs in `SCHEDULER_DATA_DIR/SCHEDULER_STORE_FILENAME`.

Design note:

- automatic retries are intentionally not enabled by default
- an unbounded retry loop could spam Home Assistant or a flaky cloud integration such as Tuya
- if retries are added later, they should be bounded and backoff-based

## Routines

The bridge also supports recurring daily routines. These are stored by the bridge instead of being written directly into Home Assistant YAML, which keeps them portable and easy to list, disable, or delete.

Example:

- `create a routine to turn on the bedroom lights every day at 7 AM`

Routine flow:

1. Claude returns an action plan with a top-level `routine`.
2. The backend validates the target, action, parameters, and routine time.
3. The routine is persisted locally on the Raspberry Pi.
4. The routine runner executes the stored plan at the next matching time.

Supported routine shape today:

```json
{
  "routine": {
    "type": "daily",
    "time": "07:00",
    "name": "Bedroom morning lights"
  }
}
```

Current routine endpoints:

- `GET /routines`
- `GET /routines?status=enabled`
- `POST /routines/{routine_id}/disable`
- `POST /routines/{routine_id}/enable`
- `DELETE /routines/{routine_id}`

Home Assistant bootstrap also exposes:

- `sensor.command_bridge_active_routines`
- `script.show_bridge_routines`
- `script.disable_bridge_routine`
- `script.enable_bridge_routine`
- `script.delete_bridge_routine`

Safety notes:

- routines cannot include high-security actions or unlock actions
- missed routines after downtime are rescheduled to the next future occurrence instead of being replayed all at once
- routine requests intentionally bypass local fast-path rules and should be interpreted by Claude

## Saved Scenes

The bridge can also store reusable scenes without writing Home Assistant YAML. Claude translates a natural-language scene creation request into a validated action plan, and the bridge persists that plan locally.

Examples:

- `create a scene called movie mode that sets the living room lights warm and dim`
- `save a scene named visitors mode that makes the office comfortable`
- `movie mode`
- `activate visitors mode`

Saved scene flow:

1. Claude returns an action plan with a top-level `saved_scene`.
2. The backend validates the targets, actions, and parameters.
3. The scene is persisted locally on the Raspberry Pi.
4. Later, a matching voice request activates the saved plan without asking Claude again.

Supported saved scene shape today:

```json
{
  "saved_scene": {
    "name": "Movie mode",
    "aliases": ["movie mode"]
  }
}
```

Current saved scene endpoints:

- `GET /saved-scenes`
- `GET /saved-scenes?status=active`
- `POST /saved-scenes/{scene_id}/activate`
- `DELETE /saved-scenes/{scene_id}`

Safety notes:

- saved scenes cannot include high-security actions or unlock actions
- scene creation requests intentionally bypass local fast-path rules and should be interpreted by Claude
- saved scenes are bridge-level presets; they do not create Home Assistant YAML scenes

## Previous State Memory

The bridge also keeps a compact per-target cache of the previous restorable state for supported device domains.

Purpose:

- support requests such as `restore the previous color`
- let Claude infer how to revert a device without forcing the user to call a dedicated restore command
- expose only compact restore data instead of the full raw Home Assistant state

How it works:

1. Right before a mutating plan is executed, the bridge captures the current state of each affected target.
2. It stores a compact snapshot with derived `restore_actions`.
3. Future Claude requests receive `previous_states` for relevant targets.
4. If the user asks to restore, revert, or go back, Claude can reuse those `restore_actions` as the basis for the new plan.

The previous-state cache persists in `STATE_MEMORY_DATA_DIR/STATE_MEMORY_STORE_FILENAME`.

## Group-Aware Execution

The bridge now distinguishes between:

- simple grouped actions that can safely target a Home Assistant group directly
- complex grouped actions that are safer when expanded to member devices

Why this matters:

- some integrations, especially cloud-backed ones such as Tuya, can behave inconsistently when a grouped light receives color or brightness changes
- a grouped `turn_off` may work fine, while a grouped `rgb_color` change may only affect part of the group

Current behavior:

- simple grouped actions such as light on/off still use the group target directly when that is safe
- complex grouped actions are expanded dynamically by reading the group's live `attributes.entity_id` members from Home Assistant
- this is generic behavior, not hardcoded to `office`, `hall`, or any specific room

Domains currently prepared for grouped expansion include:

- `light` for complex `turn_on` requests with `brightness`, `brightness_pct`, `rgb_color`, or `color_temp_kelvin`
- `fan` for `set_fan_percentage`
- `cover` for `set_cover_position`
- `climate` for `set_temperature` and `set_hvac_mode`
- `media_player` for volume and playback transport actions
- `select` and `input_select` for `select_option`
- `number` and `input_number` for `set_value`

This means future grouped rooms or zones can usually reuse the same backend logic without Python changes, as long as Home Assistant exposes them as normal grouped entities.

## Allowed Actions

The backend now validates by target capabilities instead of a tiny hardcoded action list.

Examples of supported actions:

- `turn_on`, `turn_off`, `get_state`
- `activate_scene`, `run_script`
- `lock`, `unlock`
- `open_cover`, `close_cover`, `stop_cover`, `set_cover_position`
- `set_temperature`, `set_hvac_mode`
- `set_fan_percentage`
- `media_play`, `media_pause`, `media_stop`, `media_next_track`, `media_previous_track`, `set_media_volume`
- `vacuum_start`, `vacuum_pause`, `vacuum_return_to_base`
- `select_option`, `set_value`

The exact allowed actions depend on the target domain and optional target overrides.

## Project Structure

- `app/main.py`: FastAPI entrypoint.
- `app/config.py`: environment-based configuration.
- `app/capabilities.py`: domain capability catalog, parameter validation, and target aliases.
- `app/models.py`: request, response, and context models.
- `app/anthropic_client.py`: official Anthropic API interpreter.
- `app/claude_code_cli.py`: Claude Code CLI headless interpreter.
- `app/interpreter_factory.py`: interpreter selection and fallback handling.
- `app/local_interpreter.py`: local English fallback rules for common requests.
- `app/home_assistant.py`: Home Assistant REST client.
- `app/orchestrator.py`: central validation and execution flow.
- `app/saved_scenes.py`: bridge-level reusable scenes and scene activation matching.
- `app/state_memory.py`: compact previous-state cache used for restore/revert requests.
- `tests/test_orchestrator.py`: unit tests for validation and interpreter behavior.
- `homeassistant_bootstrap/`: Home Assistant config snippets used on the Raspberry Pi.
- `scripts/`: deployment and helper scripts for the Raspberry Pi.

## Configuration

1. Create a virtual environment.
2. Install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

3. Copy the environment file:

```bash
cp .env.example .env
```

4. Fill in:

- `HOME_ASSISTANT_URL`
- `HOME_ASSISTANT_TOKEN`
- `STATE_MEMORY_ENABLED`
- `AUTO_DISCOVER_ENTITIES`
- `AUTO_DISCOVER_DOMAINS`
- `IGNORED_ENTITIES` if you want to hide stale or test entities
- `ALLOWED_SCENES`
- `ALLOWED_SCRIPTS`
- `TARGET_OVERRIDES_JSON` if you want custom aliases, security levels, or per-target action restrictions

`ANTHROPIC_API_KEY` is optional. If it is not configured, the backend can still run with local rules or Claude Code CLI depending on `INTERPRETER_MODE`.

When the bridge runs in Docker on the same Raspberry Pi as Home Assistant, a stable
choice for `HOME_ASSISTANT_URL` is the Docker host gateway:

```bash
HOME_ASSISTANT_URL=http://172.17.0.1:8123
```

When you run the bridge locally from your Mac instead, use the Raspberry Pi LAN
address or a resolvable hostname such as `homeassistant.local`.

## Interpreter Modes

- `local_rules`: use only the local fallback rules.
- `anthropic_api`: use the official Anthropic API.
- `claude_cli`: use Claude Code CLI and fall back to local rules if Claude is unavailable or out of quota.
- `auto`: prefer Anthropic API when an API key is present, otherwise try Claude Code CLI with local fallback.

## Capability Catalog

The backend is prepared for future growth by mapping allowed actions and parameters by domain.

Currently supported domains include:

- `light`
- `switch`
- `input_boolean`
- `fan`
- `lock`
- `cover`
- `climate`
- `media_player`
- `vacuum`
- `select`
- `input_select`
- `number`
- `input_number`
- `scene`
- `script`
- `sensor`
- `binary_sensor`

In most cases, adding a new device in one of these domains does not require Python changes.

When `AUTO_DISCOVER_ENTITIES=1`, the backend automatically discovers supported entities from live Home Assistant state on each request. That means newly added lights, locks, covers, fans, and similar entities can become available to Claude without editing `.env`.

## Target Overrides

The backend automatically creates portable aliases from entity IDs and Home Assistant friendly names. For example, `light.living_room_lamp` can match phrases like `living room` and `living room lamp` without Python changes.

`TARGET_OVERRIDES_JSON` is only needed when you want custom nicknames, security levels, or per-target action restrictions.

Example:

```json
{
  "light.living_room": {
    "aliases": ["main room", "lounge lights"],
    "security": "normal"
  },
  "lock.front_door": {
    "aliases": ["front door", "main door"],
    "actions": ["lock", "unlock", "get_state"],
    "security": "high"
  }
}
```

## Claude Code CLI on Raspberry Pi

If you want to use your Claude subscription instead of paying for API usage:

1. Install `claude` on the Raspberry Pi host.
2. Sign in once as user `lucas`.
3. Deploy the updated backend.

The container mounts:

- `/home/lucas`

The Claude subprocess inside the container uses `CLAUDE_CLI_HOME` to point at the Raspberry Pi host home directory. This avoids stale single-file mounts when Claude refreshes its session on the host.

### Recommended Low-Usage Claude Code Settings

For this smart-home bridge, treat Claude Code as a one-shot intent compiler, not a general agent.

Recommended settings:

- `INTERPRETER_MODE=claude_cli`
- `CLAUDE_CLI_DISABLE_AUTO_MEMORY=1`
- `CLAUDE_CLI_HOME=/home/claude-host-home`
- `FAST_PATH_LOCAL_FIRST=1`
- keep `CLAUDE.md` concise
- prefer `AUTO_DISCOVER_ENTITIES=1` for real devices
- keep `IGNORED_ENTITIES`, `ALLOWED_SCENES`, and `ALLOWED_SCRIPTS` tight
- send only a small subset of relevant targets and visible states per request

The backend now automatically:

- disables Claude Code auto-memory by default
- tries local rules first for simple direct commands, including explicit color or brightness changes
- trims prompt targets to a capped subset
- trims visible state payloads
- removes duplicated per-request rules from the CLI call

## Running Locally

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Quick health check:

```bash
curl http://127.0.0.1:8000/health
```

If no model-backed interpreter is active, you should see:

```json
{"status":"ok","interpreter":"local_rules"}
```

## Running with Docker

```bash
cp .env.example .env
docker compose up --build
```

To keep everything ready for Claude Code CLI but continue using local rules for now:

```bash
INTERPRETER_MODE=local_rules
```

When you want to activate Claude Code CLI:

```bash
INTERPRETER_MODE=claude_cli
```

## Quick Raspberry Pi Mode Switch

If the updated backend is already deployed, you can switch interpreter mode without manually editing `.env`:

```bash
expect scripts/set_pi_interpreter_mode.expect <password> <host> <remote_dir> claude_cli
```

Example for a Raspberry Pi:

```bash
expect scripts/set_pi_interpreter_mode.expect <password> <host> /home/lucas/ha-command-bridge claude_cli
```

To switch back to local rules:

```bash
expect scripts/set_pi_interpreter_mode.expect <password> <host> /home/lucas/ha-command-bridge local_rules
```

## Main Endpoint

### `POST /commands/interpret`

Request:

```json
{
  "text": "turn off the office lights",
  "dry_run": false
}
```

Example response:

```json
{
  "text": "turn off the office lights",
  "assistant_response": "Done. I turned off office.",
  "actions": [
    {
      "action": "turn_off",
      "target": "light.office",
      "parameters": {}
    }
  ],
  "executed": true,
  "dry_run": false,
  "results": [
    {
      "service": "light.turn_off",
      "target": {
        "entity_id": "light.office"
      },
      "response": []
    }
  ]
}
```

`assistant_response` is intended to be the user-facing confirmation text. Today it is shown in Assist responses and can also be spoken locally through the Raspberry Pi audio output when enabled.

## Home Assistant Helpers for Scheduled Jobs

The Home Assistant bootstrap config now includes:

- a REST sensor named `sensor.command_bridge_pending_jobs`
- a script named `script.show_scheduled_bridge_jobs`
- a script named `script.cancel_scheduled_bridge_job`

That lets an end user:

- view pending scheduled jobs from Home Assistant
- copy a `job_id`
- cancel the selected scheduled job from Home Assistant

## Adding Devices Later

For a new entity inside an already supported domain:

1. Add the device to Home Assistant.
2. Give it a clear English name in Home Assistant.
3. If auto-discovery is enabled, the backend will pick it up automatically on the next request.

## Managing Raspberry Pi Wi-Fi

For Ubuntu Server on the Raspberry Pi, the easiest long-term setup is:

- `NetworkManager` as the Netplan renderer
- `nmcli` for scripted changes
- `nmtui` for a simple text UI when you have local terminal access

The repo includes a helper script that wraps the common Wi-Fi tasks:

```bash
expect scripts/install_pi_wifi_manager.expect <password> <host> <project_dir>
```

That installs `wifi-manager` on the Raspberry Pi under `/usr/local/bin`.

Useful commands after `NetworkManager` is active:

```bash
wifi-manager list
wifi-manager choose
wifi-manager connect "YourWifiName"
wifi-manager status
sudo nmtui
```

Recommended one-time migration for Ubuntu Server if the Pi still uses `renderer: networkd`:

1. Install `network-manager`
2. Disable cloud-init network rewrites by creating `/etc/cloud/cloud.cfg.d/99-disable-network-config.cfg` with:

```yaml
network:
  config: disabled
```

3. Switch `/etc/netplan/50-cloud-init.yaml` to `renderer: NetworkManager`
4. Keep the current Wi-Fi under `wifis:` during the migration
5. Apply the change with `sudo netplan try` or `sudo netplan apply`

Useful references:

- Netplan YAML reference: https://netplan.readthedocs.io/en/latest/netplan-yaml/
- Netplan `try`: https://netplan.readthedocs.io/en/latest/netplan-try/
- `nmcli`: https://networkmanager.dev/docs/api/latest/nmcli.html
- `nmtui`: https://networkmanager.dev/docs/api/latest/nmtui.html
4. Add optional aliases or restrictions in `TARGET_OVERRIDES_JSON` only if you want custom behavior.

You only need code changes when introducing a completely new domain or action family that is not represented in `app/capabilities.py`.

## Home Assistant Integration

The most stable setup is:

1. Standard smart-home control goes through Home Assistant directly.
2. More complex commands are forwarded to this backend.
3. The backend enriches or interprets those requests and calls back into Home Assistant.

The `homeassistant_bootstrap/automations.yaml` file forwards captured Assist text to the backend with:

- `{command_text}`

To force the intelligent interpreter instead of local fast-path rules, prefix the request with `claude`.

Examples:

- `claude change the office lights to a comfortable color for visitors`
- `claude good morning`

The backend strips the `claude` prefix before sending the request to Claude. This escape hatch intentionally skips local shortcuts, including the weather briefing shortcut, and does not fall back to local rules if Claude is unavailable.

## Security

- Claude never executes Home Assistant services directly.
- Claude Code CLI never executes Home Assistant services directly.
- Local rules are limited to known patterns and allowed targets.
- Only allowed actions, targets, and parameters are accepted.
- Ambiguous or unsafe requests are rejected.
- `dry_run` lets you inspect the intent without touching a device.

## Testing

Syntax check:

```bash
python3 -m compileall app tests
```

Unit tests:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```
