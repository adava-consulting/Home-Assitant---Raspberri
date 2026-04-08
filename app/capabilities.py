from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.errors import ValidationError


@dataclass(frozen=True)
class ParameterSpec:
    kind: str
    min_value: float | None = None
    max_value: float | None = None
    allowed_values: tuple[str, ...] = ()
    list_length: int | None = None
    item_min: float | None = None
    item_max: float | None = None

    def validate(self, parameter_name: str, value: Any) -> None:
        if self.kind == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValidationError(f"Parameter '{parameter_name}' must be an integer.")
            self._validate_numeric_range(parameter_name, float(value))
            return

        if self.kind == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValidationError(f"Parameter '{parameter_name}' must be a number.")
            self._validate_numeric_range(parameter_name, float(value))
            return

        if self.kind == "string":
            if not isinstance(value, str):
                raise ValidationError(f"Parameter '{parameter_name}' must be a string.")
            if self.allowed_values and value not in self.allowed_values:
                raise ValidationError(
                    f"Parameter '{parameter_name}' must be one of {sorted(self.allowed_values)}."
                )
            return

        if self.kind == "int_list":
            if not isinstance(value, (list, tuple)):
                raise ValidationError(f"Parameter '{parameter_name}' must be a list.")
            if self.list_length is not None and len(value) != self.list_length:
                raise ValidationError(
                    f"Parameter '{parameter_name}' must contain exactly {self.list_length} items."
                )
            for item in value:
                if isinstance(item, bool) or not isinstance(item, int):
                    raise ValidationError(f"Parameter '{parameter_name}' must contain integers only.")
                if self.item_min is not None and item < self.item_min:
                    raise ValidationError(
                        f"Parameter '{parameter_name}' items must be >= {self.item_min}."
                    )
                if self.item_max is not None and item > self.item_max:
                    raise ValidationError(
                        f"Parameter '{parameter_name}' items must be <= {self.item_max}."
                    )
            return

        raise ValidationError(f"Unsupported parameter validator kind: {self.kind}")

    def to_prompt_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"kind": self.kind}
        if self.min_value is not None:
            data["min"] = self.min_value
        if self.max_value is not None:
            data["max"] = self.max_value
        if self.allowed_values:
            data["allowed_values"] = list(self.allowed_values)
        if self.list_length is not None:
            data["list_length"] = self.list_length
        if self.item_min is not None:
            data["item_min"] = self.item_min
        if self.item_max is not None:
            data["item_max"] = self.item_max
        return data

    def _validate_numeric_range(self, parameter_name: str, value: float) -> None:
        if self.min_value is not None and value < self.min_value:
            raise ValidationError(f"Parameter '{parameter_name}' must be >= {self.min_value}.")
        if self.max_value is not None and value > self.max_value:
            raise ValidationError(f"Parameter '{parameter_name}' must be <= {self.max_value}.")


@dataclass(frozen=True)
class ActionDefinition:
    action: str
    service_domain: str | None
    service_name: str | None
    parameter_specs: dict[str, ParameterSpec] = field(default_factory=dict)

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "service_domain": self.service_domain,
            "service_name": self.service_name,
            "parameters": {
                parameter_name: spec.to_prompt_dict()
                for parameter_name, spec in self.parameter_specs.items()
            },
        }


@dataclass(frozen=True)
class TargetCapabilities:
    target_id: str
    kind: str
    domain: str
    actions: dict[str, ActionDefinition]
    aliases: tuple[str, ...] = ()
    security: str = "normal"

    def action_names(self) -> list[str]:
        return sorted(self.actions)

    def action_definition(self, action: str) -> ActionDefinition | None:
        return self.actions.get(action)

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "domain": self.domain,
            "actions": {
                action_name: definition.to_prompt_dict()
                for action_name, definition in sorted(self.actions.items())
            },
            "aliases": sorted(set(self.aliases)),
            "security": self.security,
        }


BRIGHTNESS = ParameterSpec(kind="integer", min_value=0, max_value=255)
BRIGHTNESS_PCT = ParameterSpec(kind="integer", min_value=0, max_value=100)
TRANSITION = ParameterSpec(kind="number", min_value=0)
RGB_COLOR = ParameterSpec(kind="int_list", list_length=3, item_min=0, item_max=255)
COLOR_TEMP_KELVIN = ParameterSpec(kind="integer", min_value=1500, max_value=9000)
PERCENTAGE = ParameterSpec(kind="integer", min_value=0, max_value=100)
POSITION = ParameterSpec(kind="integer", min_value=0, max_value=100)
TEMPERATURE = ParameterSpec(kind="number", min_value=5, max_value=35)
VOLUME_LEVEL = ParameterSpec(kind="number", min_value=0, max_value=1)
TEXT_OPTION = ParameterSpec(kind="string")
NUMERIC_VALUE = ParameterSpec(kind="number")


ENTITY_DOMAIN_ACTIONS: dict[str, dict[str, ActionDefinition]] = {
    "light": {
        "turn_on": ActionDefinition(
            action="turn_on",
            service_domain=None,
            service_name="turn_on",
            parameter_specs={
                "brightness": BRIGHTNESS,
                "brightness_pct": BRIGHTNESS_PCT,
                "rgb_color": RGB_COLOR,
                "color_temp_kelvin": COLOR_TEMP_KELVIN,
                "transition": TRANSITION,
            },
        ),
        "turn_off": ActionDefinition(
            action="turn_off",
            service_domain=None,
            service_name="turn_off",
            parameter_specs={"transition": TRANSITION},
        ),
        "get_state": ActionDefinition(action="get_state", service_domain=None, service_name=None),
    },
    "switch": {
        "turn_on": ActionDefinition(action="turn_on", service_domain=None, service_name="turn_on"),
        "turn_off": ActionDefinition(action="turn_off", service_domain=None, service_name="turn_off"),
        "get_state": ActionDefinition(action="get_state", service_domain=None, service_name=None),
    },
    "input_boolean": {
        "turn_on": ActionDefinition(action="turn_on", service_domain=None, service_name="turn_on"),
        "turn_off": ActionDefinition(action="turn_off", service_domain=None, service_name="turn_off"),
        "get_state": ActionDefinition(action="get_state", service_domain=None, service_name=None),
    },
    "fan": {
        "turn_on": ActionDefinition(action="turn_on", service_domain=None, service_name="turn_on"),
        "turn_off": ActionDefinition(action="turn_off", service_domain=None, service_name="turn_off"),
        "set_fan_percentage": ActionDefinition(
            action="set_fan_percentage",
            service_domain=None,
            service_name="set_percentage",
            parameter_specs={"percentage": PERCENTAGE},
        ),
        "get_state": ActionDefinition(action="get_state", service_domain=None, service_name=None),
    },
    "lock": {
        "lock": ActionDefinition(action="lock", service_domain=None, service_name="lock"),
        "unlock": ActionDefinition(action="unlock", service_domain=None, service_name="unlock"),
        "get_state": ActionDefinition(action="get_state", service_domain=None, service_name=None),
    },
    "cover": {
        "open_cover": ActionDefinition(action="open_cover", service_domain=None, service_name="open_cover"),
        "close_cover": ActionDefinition(action="close_cover", service_domain=None, service_name="close_cover"),
        "stop_cover": ActionDefinition(action="stop_cover", service_domain=None, service_name="stop_cover"),
        "set_cover_position": ActionDefinition(
            action="set_cover_position",
            service_domain=None,
            service_name="set_cover_position",
            parameter_specs={"position": POSITION},
        ),
        "get_state": ActionDefinition(action="get_state", service_domain=None, service_name=None),
    },
    "climate": {
        "turn_on": ActionDefinition(action="turn_on", service_domain=None, service_name="turn_on"),
        "turn_off": ActionDefinition(action="turn_off", service_domain=None, service_name="turn_off"),
        "set_temperature": ActionDefinition(
            action="set_temperature",
            service_domain=None,
            service_name="set_temperature",
            parameter_specs={"temperature": TEMPERATURE},
        ),
        "set_hvac_mode": ActionDefinition(
            action="set_hvac_mode",
            service_domain=None,
            service_name="set_hvac_mode",
            parameter_specs={"hvac_mode": TEXT_OPTION},
        ),
        "get_state": ActionDefinition(action="get_state", service_domain=None, service_name=None),
    },
    "media_player": {
        "turn_on": ActionDefinition(action="turn_on", service_domain=None, service_name="turn_on"),
        "turn_off": ActionDefinition(action="turn_off", service_domain=None, service_name="turn_off"),
        "media_play": ActionDefinition(action="media_play", service_domain=None, service_name="media_play"),
        "media_pause": ActionDefinition(action="media_pause", service_domain=None, service_name="media_pause"),
        "media_stop": ActionDefinition(action="media_stop", service_domain=None, service_name="media_stop"),
        "media_next_track": ActionDefinition(
            action="media_next_track",
            service_domain=None,
            service_name="media_next_track",
        ),
        "media_previous_track": ActionDefinition(
            action="media_previous_track",
            service_domain=None,
            service_name="media_previous_track",
        ),
        "set_media_volume": ActionDefinition(
            action="set_media_volume",
            service_domain=None,
            service_name="volume_set",
            parameter_specs={"volume_level": VOLUME_LEVEL},
        ),
        "get_state": ActionDefinition(action="get_state", service_domain=None, service_name=None),
    },
    "vacuum": {
        "vacuum_start": ActionDefinition(action="vacuum_start", service_domain=None, service_name="start"),
        "vacuum_pause": ActionDefinition(action="vacuum_pause", service_domain=None, service_name="pause"),
        "vacuum_return_to_base": ActionDefinition(
            action="vacuum_return_to_base",
            service_domain=None,
            service_name="return_to_base",
        ),
        "get_state": ActionDefinition(action="get_state", service_domain=None, service_name=None),
    },
    "select": {
        "select_option": ActionDefinition(
            action="select_option",
            service_domain=None,
            service_name="select_option",
            parameter_specs={"option": TEXT_OPTION},
        ),
        "get_state": ActionDefinition(action="get_state", service_domain=None, service_name=None),
    },
    "input_select": {
        "select_option": ActionDefinition(
            action="select_option",
            service_domain=None,
            service_name="select_option",
            parameter_specs={"option": TEXT_OPTION},
        ),
        "get_state": ActionDefinition(action="get_state", service_domain=None, service_name=None),
    },
    "number": {
        "set_value": ActionDefinition(
            action="set_value",
            service_domain=None,
            service_name="set_value",
            parameter_specs={"value": NUMERIC_VALUE},
        ),
        "get_state": ActionDefinition(action="get_state", service_domain=None, service_name=None),
    },
    "input_number": {
        "set_value": ActionDefinition(
            action="set_value",
            service_domain=None,
            service_name="set_value",
            parameter_specs={"value": NUMERIC_VALUE},
        ),
        "get_state": ActionDefinition(action="get_state", service_domain=None, service_name=None),
    },
    "sensor": {
        "get_state": ActionDefinition(action="get_state", service_domain=None, service_name=None),
    },
    "binary_sensor": {
        "get_state": ActionDefinition(action="get_state", service_domain=None, service_name=None),
    },
}

SCENE_ACTIONS = {
    "activate_scene": ActionDefinition(
        action="activate_scene",
        service_domain="scene",
        service_name="turn_on",
    )
}

SCRIPT_ACTIONS = {
    "run_script": ActionDefinition(
        action="run_script",
        service_domain="script",
        service_name="turn_on",
    )
}

DEFAULT_TARGET_ALIASES: dict[str, set[str]] = {}

GROUP_EXPANSION_ACTIONS: dict[str, set[str]] = {
    "light": {"turn_on"},
    "fan": {"set_fan_percentage"},
    "cover": {"set_cover_position"},
    "climate": {"set_temperature", "set_hvac_mode"},
    "media_player": {
        "set_media_volume",
        "media_play",
        "media_pause",
        "media_stop",
        "media_next_track",
        "media_previous_track",
    },
    "select": {"select_option"},
    "input_select": {"select_option"},
    "number": {"set_value"},
    "input_number": {"set_value"},
}


def build_target_capabilities(settings: Any) -> dict[str, TargetCapabilities]:
    return build_target_capabilities_from_lists(
        allowed_entities=getattr(settings, "allowed_entities", []),
        allowed_scenes=getattr(settings, "allowed_scenes", []),
        allowed_scripts=getattr(settings, "allowed_scripts", []),
        target_overrides=getattr(settings, "target_overrides", {}),
    )


def build_target_capabilities_from_lists(
    *,
    allowed_entities: list[str],
    allowed_scenes: list[str],
    allowed_scripts: list[str],
    target_overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, TargetCapabilities]:
    overrides = target_overrides or {}
    target_capabilities: dict[str, TargetCapabilities] = {}

    for entity_id in allowed_entities:
        domain, _ = entity_id.split(".", 1)
        action_definitions = dict(ENTITY_DOMAIN_ACTIONS.get(domain, {}))
        if not action_definitions:
            action_definitions["get_state"] = ActionDefinition(
                action="get_state",
                service_domain=None,
                service_name=None,
            )

        override = overrides.get(entity_id, {})
        action_names = _normalized_action_names(override.get("actions"))
        if action_names:
            action_definitions = {
                action_name: action_definitions[action_name]
                for action_name in action_names
                if action_name in action_definitions
            }

        target_capabilities[entity_id] = TargetCapabilities(
            target_id=entity_id,
            kind="entity",
            domain=domain,
            actions=action_definitions,
            aliases=tuple(sorted(_combined_aliases(entity_id, override, "entity"))),
            security=_normalized_security(override.get("security")),
        )

    for scene_id in allowed_scenes:
        override = overrides.get(scene_id, {})
        target_capabilities[scene_id] = TargetCapabilities(
            target_id=scene_id,
            kind="scene",
            domain="scene",
            actions=SCENE_ACTIONS,
            aliases=tuple(sorted(_combined_aliases(scene_id, override, "scene"))),
            security=_normalized_security(override.get("security")),
        )

    for script_id in allowed_scripts:
        override = overrides.get(script_id, {})
        target_capabilities[script_id] = TargetCapabilities(
            target_id=script_id,
            kind="script",
            domain="script",
            actions=SCRIPT_ACTIONS,
            aliases=tuple(sorted(_combined_aliases(script_id, override, "script"))),
            security=_normalized_security(override.get("security")),
        )

    return target_capabilities


def resolve_action_definition(target_id: str, action: str) -> ActionDefinition | None:
    domain, _ = target_id.split(".", 1)

    if domain == "scene":
        return SCENE_ACTIONS.get(action)
    if domain == "script":
        return SCRIPT_ACTIONS.get(action)

    return ENTITY_DOMAIN_ACTIONS.get(domain, {}).get(action)


def should_expand_group_action(target_id: str, action: str, parameters: dict[str, Any] | None = None) -> bool:
    domain, _ = target_id.split(".", 1)
    parameters = parameters or {}
    expandable_actions = GROUP_EXPANSION_ACTIONS.get(domain, set())
    if action not in expandable_actions:
        return False

    if domain == "light":
        complex_light_keys = {"brightness", "brightness_pct", "rgb_color", "color_temp_kelvin"}
        return bool(complex_light_keys & set(parameters))

    return True


def matching_keywords_for_target(
    target_id: str,
    aliases: tuple[str, ...] = (),
    kind: str = "entity",
) -> set[str]:
    _, slug = target_id.split(".", 1)
    keywords = _slug_keywords(slug, kind)
    keywords |= DEFAULT_TARGET_ALIASES.get(slug, set())
    keywords |= {alias.strip() for alias in aliases if alias and alias.strip()}
    return {keyword for keyword in keywords if keyword}


def _slug_keywords(slug: str, kind: str) -> set[str]:
    parts = [part for part in slug.split("_") if part]
    keywords = {slug, slug.replace("_", " ")}

    if kind == "entity":
        keywords |= set(parts)

    for start in range(len(parts)):
        for end in range(start + 2, len(parts) + 1):
            keywords.add(" ".join(parts[start:end]))

    return {keyword for keyword in keywords if keyword}


def _combined_aliases(target_id: str, override: dict[str, Any], kind: str) -> set[str]:
    return matching_keywords_for_target(
        target_id,
        tuple(_normalized_aliases(override.get("aliases"))),
        kind=kind,
    )


def _normalized_aliases(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _normalized_action_names(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _normalized_security(value: Any) -> str:
    if not isinstance(value, str):
        return "normal"
    security = value.strip().lower()
    if security in {"low", "normal", "high"}:
        return security
    return "normal"
