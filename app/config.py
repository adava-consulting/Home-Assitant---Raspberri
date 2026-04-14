from pathlib import Path

from functools import lru_cache
import json

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    home_assistant_url: str = Field(..., alias="HOME_ASSISTANT_URL")
    home_assistant_token: str = Field(..., alias="HOME_ASSISTANT_TOKEN")
    home_assistant_state_cache_ttl_seconds: float = Field(
        0.0,
        alias="HOME_ASSISTANT_STATE_CACHE_TTL_SECONDS",
    )
    command_bridge_api_token: str = Field("", alias="COMMAND_BRIDGE_API_TOKEN")
    command_bridge_api_header_name: str = Field(
        "X-Bridge-Token",
        alias="COMMAND_BRIDGE_API_HEADER_NAME",
    )
    interpreter_mode: str = Field("local_rules", alias="INTERPRETER_MODE")
    anthropic_api_key: str = Field("", alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field("claude-sonnet-4-20250514", alias="ANTHROPIC_MODEL")
    claude_cli_command: str = Field("claude", alias="CLAUDE_CLI_COMMAND")
    claude_cli_cwd: str = Field("/app", alias="CLAUDE_CLI_CWD")
    claude_cli_home: str = Field("/home/claude-host-home", alias="CLAUDE_CLI_HOME")
    claude_cli_timeout_seconds: float = Field(45.0, alias="CLAUDE_CLI_TIMEOUT_SECONDS")
    claude_cli_disable_auto_memory: bool = Field(True, alias="CLAUDE_CLI_DISABLE_AUTO_MEMORY")
    claude_cli_max_prompt_targets: int = Field(12, alias="CLAUDE_CLI_MAX_PROMPT_TARGETS")
    claude_cli_max_visible_states: int = Field(8, alias="CLAUDE_CLI_MAX_VISIBLE_STATES")
    fast_path_local_first: bool = Field(True, alias="FAST_PATH_LOCAL_FIRST")
    local_timezone: str = Field("UTC", alias="LOCAL_TIMEZONE")
    scheduling_enabled: bool = Field(True, alias="SCHEDULING_ENABLED")
    scheduler_poll_interval_seconds: float = Field(1.0, alias="SCHEDULER_POLL_INTERVAL_SECONDS")
    scheduler_data_dir: str = Field(
        "/home/claude-host-home/ha-command-bridge-data",
        alias="SCHEDULER_DATA_DIR",
    )
    scheduler_store_filename: str = Field(
        "scheduled_commands.json",
        alias="SCHEDULER_STORE_FILENAME",
    )
    routines_enabled: bool = Field(True, alias="ROUTINES_ENABLED")
    routines_poll_interval_seconds: float = Field(30.0, alias="ROUTINES_POLL_INTERVAL_SECONDS")
    routines_data_dir: str = Field(
        "/home/claude-host-home/ha-command-bridge-data",
        alias="ROUTINES_DATA_DIR",
    )
    routines_store_filename: str = Field(
        "routines.json",
        alias="ROUTINES_STORE_FILENAME",
    )
    saved_scenes_enabled: bool = Field(True, alias="SAVED_SCENES_ENABLED")
    saved_scenes_data_dir: str = Field(
        "/home/claude-host-home/ha-command-bridge-data",
        alias="SAVED_SCENES_DATA_DIR",
    )
    saved_scenes_store_filename: str = Field(
        "saved_scenes.json",
        alias="SAVED_SCENES_STORE_FILENAME",
    )
    state_memory_enabled: bool = Field(True, alias="STATE_MEMORY_ENABLED")
    state_memory_data_dir: str = Field(
        "/home/claude-host-home/ha-command-bridge-data",
        alias="STATE_MEMORY_DATA_DIR",
    )
    state_memory_store_filename: str = Field(
        "previous_state_cache.json",
        alias="STATE_MEMORY_STORE_FILENAME",
    )
    auto_discover_entities: bool = Field(True, alias="AUTO_DISCOVER_ENTITIES")
    auto_discover_domains_raw: str = Field(
        "light,switch,lock,cover,fan,climate,media_player,vacuum,sensor,binary_sensor",
        alias="AUTO_DISCOVER_DOMAINS",
    )
    auto_discover_include_unavailable: bool = Field(
        False,
        alias="AUTO_DISCOVER_INCLUDE_UNAVAILABLE",
    )
    ignored_entities_raw: str = Field("", alias="IGNORED_ENTITIES")
    allowed_entities_raw: str = Field("", alias="ALLOWED_ENTITIES")
    allowed_scenes_raw: str = Field("", alias="ALLOWED_SCENES")
    allowed_scripts_raw: str = Field("", alias="ALLOWED_SCRIPTS")
    voice_model_file: str = Field("", alias="VOICE_MODEL_FILE")
    target_overrides_raw: str = Field("{}", alias="TARGET_OVERRIDES_JSON")
    health_monitored_entities_raw: str = Field("", alias="HEALTH_MONITORED_ENTITIES")
    request_timeout_seconds: float = Field(20.0, alias="REQUEST_TIMEOUT_SECONDS")
    audio_response_enabled: bool = Field(False, alias="AUDIO_RESPONSE_ENABLED")
    audio_response_engine: str = Field("auto", alias="AUDIO_RESPONSE_ENGINE")
    audio_response_voice: str = Field("en-us", alias="AUDIO_RESPONSE_VOICE")
    audio_response_speed: int = Field(155, alias="AUDIO_RESPONSE_SPEED")
    audio_response_device: str = Field("plughw:0,0", alias="AUDIO_RESPONSE_DEVICE")
    audio_response_cache_enabled: bool = Field(True, alias="AUDIO_RESPONSE_CACHE_ENABLED")
    audio_response_cache_dir: str = Field(
        "/home/claude-host-home/ha-command-bridge-data/audio-cache",
        alias="AUDIO_RESPONSE_CACHE_DIR",
    )
    audio_response_fast_ack_for_local: bool = Field(
        True,
        alias="AUDIO_RESPONSE_FAST_ACK_FOR_LOCAL",
    )
    audio_response_local_ack_mode: str = Field(
        "descriptive",
        alias="AUDIO_RESPONSE_LOCAL_ACK_MODE",
    )
    audio_response_fast_ack_text: str = Field("Done.", alias="AUDIO_RESPONSE_FAST_ACK_TEXT")
    kokoro_model_path: str = Field("", alias="KOKORO_MODEL_PATH")
    kokoro_voices_path: str = Field("", alias="KOKORO_VOICES_PATH")
    kokoro_voice: str = Field("af_heart", alias="KOKORO_VOICE")
    kokoro_lang: str = Field("en-us", alias="KOKORO_LANG")
    kokoro_speed: float = Field(1.0, alias="KOKORO_SPEED")
    kokoro_sample_rate: int = Field(24000, alias="KOKORO_SAMPLE_RATE")
    kokoro_warmup_enabled: bool = Field(True, alias="KOKORO_WARMUP_ENABLED")
    elevenlabs_api_key: str = Field("", alias="ELEVENLABS_API_KEY")
    elevenlabs_voice_id: str = Field("pNInz6obpgDQGcFmaJgB", alias="ELEVENLABS_VOICE_ID")
    elevenlabs_model_id: str = Field("eleven_flash_v2_5", alias="ELEVENLABS_MODEL_ID")
    elevenlabs_output_format: str = Field(
        "mp3_22050_32",
        alias="ELEVENLABS_OUTPUT_FORMAT",
    )
    elevenlabs_stability: float = Field(0.45, alias="ELEVENLABS_STABILITY")
    elevenlabs_similarity_boost: float = Field(
        0.8,
        alias="ELEVENLABS_SIMILARITY_BOOST",
    )
    elevenlabs_style: float = Field(0.1, alias="ELEVENLABS_STYLE")
    elevenlabs_use_speaker_boost: bool = Field(
        True,
        alias="ELEVENLABS_USE_SPEAKER_BOOST",
    )
    elevenlabs_speed: float = Field(1.0, alias="ELEVENLABS_SPEED")
    piper_command: str = Field("piper", alias="PIPER_COMMAND")
    piper_model_path: str = Field("", alias="PIPER_MODEL_PATH")
    piper_speaker: int = Field(0, alias="PIPER_SPEAKER")
    piper_length_scale: float = Field(1.0, alias="PIPER_LENGTH_SCALE")
    log_level: str = Field("INFO", alias="LOG_LEVEL")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def _split_csv(value: str | list[str]) -> list[str]:
    if isinstance(value, list):
        return value
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _split_or_file_list(raw_value: str | list[str], file_values: object) -> list[str]:
    env_values = _split_csv(raw_value)
    if env_values:
        return env_values
    if isinstance(file_values, list):
        return [item.strip() for item in file_values if isinstance(item, str) and item.strip()]
    return []


def _load_json_file(path_value: str) -> dict:
    path = Path(path_value).expanduser()
    if not path_value.strip() or not path.exists() or not path.is_file():
        return {}

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    if isinstance(payload, dict):
        return payload
    return {}


@property
def allowed_entities(self: Settings) -> list[str]:
    return _split_or_file_list(
        self.allowed_entities_raw,
        self.voice_model.get("allowed_entities"),
    )


@property
def allowed_scenes(self: Settings) -> list[str]:
    return _split_or_file_list(
        self.allowed_scenes_raw,
        self.voice_model.get("allowed_scenes"),
    )


@property
def allowed_scripts(self: Settings) -> list[str]:
    return _split_or_file_list(
        self.allowed_scripts_raw,
        self.voice_model.get("allowed_scripts"),
    )


Settings.allowed_entities = allowed_entities
Settings.allowed_scenes = allowed_scenes
Settings.allowed_scripts = allowed_scripts


@property
def auto_discover_domains(self: Settings) -> list[str]:
    return [domain.lower() for domain in _split_csv(self.auto_discover_domains_raw)]


@property
def ignored_entities(self: Settings) -> list[str]:
    return _split_or_file_list(
        self.ignored_entities_raw,
        self.voice_model.get("ignored_entities"),
    )


Settings.auto_discover_domains = auto_discover_domains
Settings.ignored_entities = ignored_entities


@property
def health_monitored_entities(self: Settings) -> list[str]:
    return _split_csv(self.health_monitored_entities_raw)


Settings.health_monitored_entities = health_monitored_entities


@property
def voice_model(self: Settings) -> dict[str, object]:
    return _load_json_file(self.voice_model_file)


Settings.voice_model = voice_model


@property
def target_overrides(self: Settings) -> dict[str, dict]:
    raw = self.target_overrides_raw.strip()
    payload: object = {}
    if raw:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {}

    file_payload = self.voice_model.get("target_overrides")
    normalized: dict[str, dict] = {}
    if isinstance(file_payload, dict):
        for target_id, override in file_payload.items():
            if not isinstance(target_id, str) or not isinstance(override, dict):
                continue
            normalized[target_id] = dict(override)

    if not isinstance(payload, dict):
        return normalized

    for target_id, override in payload.items():
        if not isinstance(target_id, str) or not isinstance(override, dict):
            continue
        normalized[target_id] = dict(override)
    return normalized


Settings.target_overrides = target_overrides


@property
def use_anthropic(self: Settings) -> bool:
    key = self.anthropic_api_key.strip()
    return bool(key) and not key.startswith("replace-with-")


Settings.use_anthropic = use_anthropic


@property
def normalized_interpreter_mode(self: Settings) -> str:
    mode = self.interpreter_mode.strip().lower()
    allowed_modes = {"local_rules", "anthropic_api", "claude_cli", "auto"}
    return mode if mode in allowed_modes else "local_rules"


Settings.normalized_interpreter_mode = normalized_interpreter_mode


@property
def bridge_auth_enabled(self: Settings) -> bool:
    return bool(self.command_bridge_api_token.strip())


Settings.bridge_auth_enabled = bridge_auth_enabled


@property
def scheduler_store_path(self: Settings) -> str:
    return str(Path(self.scheduler_data_dir) / self.scheduler_store_filename)


Settings.scheduler_store_path = scheduler_store_path


@property
def routines_store_path(self: Settings) -> str:
    return str(Path(self.routines_data_dir) / self.routines_store_filename)


Settings.routines_store_path = routines_store_path


@property
def saved_scenes_store_path(self: Settings) -> str:
    return str(Path(self.saved_scenes_data_dir) / self.saved_scenes_store_filename)


Settings.saved_scenes_store_path = saved_scenes_store_path


@property
def state_memory_store_path(self: Settings) -> str:
    return str(Path(self.state_memory_data_dir) / self.state_memory_store_filename)


Settings.state_memory_store_path = state_memory_store_path
