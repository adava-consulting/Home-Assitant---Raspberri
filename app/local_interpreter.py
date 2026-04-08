from __future__ import annotations

import re
import unicodedata

from app.capabilities import (
    ActionDefinition,
    TargetCapabilities,
    build_target_capabilities_from_lists,
    matching_keywords_for_target,
)
from app.errors import ValidationError
from app.models import ActionPlan, ClaudeContext, Intent, ScheduleSpec


RELATIVE_SCHEDULE_PATTERNS = (
    (re.compile(r"\bin\s+(\d+)\s+second(?:s)?\b"), 1),
    (re.compile(r"\bin\s+(\d+)\s+minute(?:s)?\b"), 60),
    (re.compile(r"\bin\s+(\d+)\s+hour(?:s)?\b"), 3600),
)

LIGHT_COLOR_VALUES: dict[str, list[int]] = {
    "red": [255, 0, 0],
    "rojo": [255, 0, 0],
    "green": [0, 255, 0],
    "verde": [0, 255, 0],
    "blue": [0, 0, 255],
    "azul": [0, 0, 255],
    "yellow": [255, 255, 0],
    "amarillo": [255, 255, 0],
    "orange": [255, 128, 0],
    "naranja": [255, 128, 0],
    "purple": [128, 0, 255],
    "violeta": [128, 0, 255],
    "morado": [128, 0, 255],
    "pink": [255, 105, 180],
    "rosa": [255, 105, 180],
    "magenta": [255, 0, 255],
    "cyan": [0, 255, 255],
    "white": [255, 255, 255],
    "blanco": [255, 255, 255],
}

LIGHT_TEMPERATURE_VALUES: dict[str, int] = {
    "warm white": 2700,
    "warm": 2700,
    "cool white": 5000,
    "cool": 5000,
    "daylight": 6500,
}

UNSUPPORTED_LIGHT_MODIFIER_PATTERNS = (
    re.compile(
        r"\b(?:to|a|color|colour|make|set|cambia|cambie|cambiar|pon|pone|poner)\s+"
        r"(?:red|rojo|green|verde|blue|azul|yellow|amarillo|orange|naranja|purple|violeta|morado|pink|rosa|magenta|cyan|white|blanco|warm|cool|daylight)\b"
    ),
    re.compile(r"\b(?:brightness|bright|dim|dimmer|warmer|cooler)\b"),
    re.compile(r"\b\d{1,3}\s*(?:%|percent|per cent)\b"),
)


def _normalize(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text.lower())
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9\s]", " ", normalized)


def _contains_phrase(text: str, phrase: str) -> bool:
    escaped = re.escape(phrase.strip())
    return bool(escaped and re.search(rf"\b{escaped}\b", text))


class LocalInterpreter:
    """Simple fallback interpreter for common English commands."""

    def __init__(self, settings):
        self._settings = settings

    async def interpret(self, text: str, context: ClaudeContext) -> ActionPlan:
        normalized = _normalize(text)
        schedule = self._extract_schedule(normalized)
        if schedule is not None:
            normalized = self._remove_schedule_phrase(normalized)
        target_capabilities = self._target_capabilities_from_context(context)

        if self._looks_like_recurring_routine_request(normalized):
            raise ValidationError("Recurring routine requests require the intelligent interpreter.")
        if self._looks_like_saved_scene_request(normalized):
            raise ValidationError("Saved scene requests require the intelligent interpreter.")

        light_parameters = self._extract_light_parameters(normalized)
        if light_parameters:
            if self._looks_like_all_home_lights(normalized):
                targets = self._all_home_light_targets(context, target_capabilities, "turn_on")
                if targets:
                    return self._multi_action_plan(
                        action="turn_on",
                        targets=targets,
                        parameters=light_parameters,
                        rationale="Matched local all-lights adjustment rule.",
                        schedule=schedule,
                    )

            target = self._find_target_by_action(
                normalized,
                target_capabilities,
                "turn_on",
                domain="light",
            )
            if target:
                return self._single_action_plan(
                    action="turn_on",
                    target=target,
                    parameters=light_parameters,
                    rationale="Matched local light adjustment rule.",
                    schedule=schedule,
                )

        if self._looks_like_restore_request(normalized):
            target = self._find_restore_target(normalized, context, target_capabilities)
            if target:
                restore_plan = self._restore_plan_from_context(
                    target=target,
                    context=context,
                    schedule=schedule,
                )
                if restore_plan is not None:
                    return restore_plan
            raise ValidationError("No previous state is available for that target.")

        scene = self._find_target_by_action(normalized, target_capabilities, "activate_scene")
        if scene:
            return self._single_action_plan(
                action="activate_scene",
                target=scene,
                rationale="Matched local scene rule.",
                schedule=schedule,
            )

        script = self._find_target_by_action(normalized, target_capabilities, "run_script")
        if script:
            return self._single_action_plan(
                action="run_script",
                target=script,
                rationale="Matched local script rule.",
                schedule=schedule,
            )

        if self._looks_like_state_request(normalized):
            target = self._find_target_by_action(normalized, target_capabilities, "get_state")
            if target:
                return self._single_action_plan(
                    action="get_state",
                    target=target,
                    rationale="Matched local state rule.",
                    schedule=schedule,
                )

        action = self._extract_action(normalized)
        if action:
            if action in {"turn_on", "turn_off"} and self._looks_like_all_home_lights(normalized):
                targets = self._all_home_light_targets(context, target_capabilities, action)
                if targets:
                    return self._multi_action_plan(
                        action=action,
                        targets=targets,
                        rationale="Matched local all-lights rule.",
                        schedule=schedule,
                    )

            if action == "turn_on" and self._has_unsupported_light_modifier(normalized):
                raise ValidationError("Local rules cannot safely handle this light modifier.")

            target = self._find_target_by_action(normalized, target_capabilities, action)
            if target:
                return self._single_action_plan(
                    action=action,
                    target=target,
                    rationale="Matched local entity rule.",
                    schedule=schedule,
                )

        raise ValidationError(
            "No local rule matched the request. Add a clearer phrase or configure an Anthropic API key."
        )

    def _single_action_plan(
        self,
        *,
        action: str,
        target: str,
        rationale: str,
        parameters: dict | None = None,
        schedule: ScheduleSpec | None = None,
    ) -> ActionPlan:
        return ActionPlan(
            actions=[
                Intent(
                    action=action,
                    target=target,
                    parameters=parameters or {},
                    rationale=rationale,
                )
            ],
            schedule=schedule,
        )

    def _multi_action_plan(
        self,
        *,
        action: str,
        targets: list[str],
        rationale: str,
        parameters: dict | None = None,
        schedule: ScheduleSpec | None = None,
    ) -> ActionPlan:
        return ActionPlan(
            actions=[
                Intent(
                    action=action,
                    target=target,
                    parameters=parameters or {},
                    rationale=rationale,
                )
                for target in targets
            ],
            schedule=schedule,
        )

    def _extract_schedule(self, text: str) -> ScheduleSpec | None:
        for pattern, multiplier in RELATIVE_SCHEDULE_PATTERNS:
            match = pattern.search(text)
            if match:
                value = int(match.group(1))
                return ScheduleSpec(type="delay", delay_seconds=value * multiplier)
        return None

    def _remove_schedule_phrase(self, text: str) -> str:
        updated = text
        for pattern, _ in RELATIVE_SCHEDULE_PATTERNS:
            updated = pattern.sub(" ", updated)
        return re.sub(r"\s+", " ", updated).strip()

    def _extract_action(self, text: str) -> str | None:
        action_keywords = (
            ("unlock", ("unlock",)),
            ("lock", ("lock",)),
            ("open_cover", ("open", "raise")),
            ("close_cover", ("close", "shut")),
            ("stop_cover", ("stop", "halt")),
            ("turn_off", ("turn off", "switch off", "power off", "deactivate", "disable", "apaga", "apague", "apagar")),
            ("turn_on", ("turn on", "switch on", "power on", "activate", "enable", "prende", "prenda", "prender", "enciende", "encienda", "encender", "cambia", "cambie", "cambiar", "pon", "pone", "poner")),
        )

        for action, keywords in action_keywords:
            if any(_contains_phrase(text, keyword) for keyword in keywords):
                return action
        return None

    def _looks_like_recurring_routine_request(self, text: str) -> bool:
        patterns = (
            r"\b(?:create|make|set\s+up|add)\s+(?:a\s+)?(?:routine|automation)\b",
            r"\b(?:every\s+day|daily|every\s+morning|every\s+afternoon|every\s+evening|every\s+night)\b",
            r"\b(?:todos\s+los\s+dias|cada\s+dia|cada\s+manana|todas\s+las\s+mananas)\b",
        )
        return any(re.search(pattern, text) for pattern in patterns)

    def _looks_like_saved_scene_request(self, text: str) -> bool:
        patterns = (
            r"\b(?:create|make|save|set\s+up|add)\s+(?:a\s+)?(?:saved\s+)?scene\b",
            r"\b(?:create|make|save|set\s+up|add)\s+.+\s+(?:as|called|named)\s+.+\s+scene\b",
            r"\b(?:crea|crear|guarda|guardar)\b.*\bescena\b",
        )
        return any(re.search(pattern, text) for pattern in patterns)

    def _extract_light_parameters(self, text: str) -> dict | None:
        parameters: dict[str, int | list[int]] = {}

        for phrase, kelvin in LIGHT_TEMPERATURE_VALUES.items():
            if _contains_phrase(text, phrase):
                parameters["color_temp_kelvin"] = kelvin
                break

        if "color_temp_kelvin" not in parameters:
            for color_name, rgb_color in LIGHT_COLOR_VALUES.items():
                if self._contains_explicit_color_request(text, color_name):
                    parameters["rgb_color"] = rgb_color
                    break

        brightness_pct = self._extract_brightness_pct(text)
        if brightness_pct is not None:
            parameters["brightness_pct"] = brightness_pct

        return parameters or None

    def _contains_explicit_color_request(self, text: str, color_name: str) -> bool:
        color = re.escape(color_name)
        return bool(
            re.search(rf"\b(?:to|a|color|colour|make|set|cambia|cambie|cambiar|pon|pone|poner)\s+(?:the\s+|el\s+|la\s+)?{color}\b", text)
            or re.search(rf"\bturn\s+(?:the\s+)?[\w\s]*\s+{color}\b", text)
        )

    def _looks_like_all_home_lights(self, text: str) -> bool:
        patterns = (
            r"\ball\s+(?:the\s+)?(?:house|home)?\s*lights\b",
            r"\bevery\s+light\b",
            r"\blights\s+(?:in|of)\s+(?:the\s+)?(?:house|home)\b",
            r"\btodas\s+las\s+luces\b",
            r"\btodas\s+la\s+luces\b",
            r"\btodos\s+los\s+focos\b",
            r"\bluces\s+de\s+(?:la\s+)?casa\b",
        )
        return any(re.search(pattern, text) for pattern in patterns)

    def _all_home_light_targets(
        self,
        context: ClaudeContext,
        target_capabilities: dict[str, TargetCapabilities],
        action: str,
    ) -> list[str]:
        light_targets = [
            target_id
            for target_id, capabilities in target_capabilities.items()
            if capabilities.domain == "light" and action in capabilities.actions
        ]
        if not light_targets:
            return []

        member_targets = set()
        group_targets = []
        states = getattr(context, "states", []) or []
        for state in states:
            entity_id = state.get("entity_id")
            if entity_id not in light_targets:
                continue
            members = state.get("attributes", {}).get("entity_id")
            if not isinstance(members, list) or not members:
                continue
            group_targets.append(entity_id)
            member_targets.update(member for member in members if isinstance(member, str))

        standalone_targets = [
            target_id
            for target_id in light_targets
            if target_id not in member_targets and target_id not in group_targets
        ]
        return sorted(set(group_targets + standalone_targets))

    def _extract_brightness_pct(self, text: str) -> int | None:
        match = re.search(r"\b(\d{1,3})\s*(?:%|percent|per cent)\b", text)
        if not match:
            return None

        value = int(match.group(1))
        if value < 0 or value > 100:
            return None
        return value

    def _has_unsupported_light_modifier(self, text: str) -> bool:
        return any(pattern.search(text) for pattern in UNSUPPORTED_LIGHT_MODIFIER_PATTERNS)

    def _looks_like_state_request(self, text: str) -> bool:
        if self._looks_like_restore_request(text):
            return False

        state_keywords = (
            "state",
            "status",
            "what is the state",
            "what is the status",
            "is it on",
            "is it off",
            "is it active",
            "is it open",
            "is it closed",
        )
        return any(_contains_phrase(text, keyword) for keyword in state_keywords)

    def _looks_like_restore_request(self, text: str) -> bool:
        restore_patterns = (
            r"\b(?:restore|return|revert|go\s+back|put\s+back)\b.*\b(?:previous|last|prior|before|original)\b",
            r"\b(?:previous|last|prior|before|original)\b.*\b(?:state|color|colour|brightness|setting|settings)\b",
            r"\b(?:restore|return|revert|go\s+back|put\s+back)\b.*\b(?:state|color|colour|brightness|setting|settings)\b",
        )
        return any(re.search(pattern, text) for pattern in restore_patterns)

    def _find_restore_target(
        self,
        text: str,
        context: ClaudeContext,
        target_capabilities: dict[str, TargetCapabilities],
    ) -> str | None:
        target = self._find_target_by_action(text, target_capabilities, "turn_on", domain="light")
        if target:
            return target

        if "light" in text or "lights" in text:
            previous_states = getattr(context, "previous_states", {}) or {}
            light_targets = [
                target_id
                for target_id in previous_states
                if isinstance(target_id, str) and target_id.startswith("light.")
            ]
            if len(light_targets) == 1:
                return light_targets[0]

            group_targets = [
                target_id
                for target_id in light_targets
                if self._previous_restore_action_count(previous_states.get(target_id, {})) > 1
            ]
            if len(group_targets) == 1:
                return group_targets[0]

            latest_group_target = self._latest_previous_state_target(group_targets, previous_states)
            if latest_group_target:
                return latest_group_target

        return None

    def _restore_plan_from_context(
        self,
        *,
        target: str,
        context: ClaudeContext,
        schedule: ScheduleSpec | None,
    ) -> ActionPlan | None:
        previous_states = getattr(context, "previous_states", {}) or {}
        previous_state = previous_states.get(target)
        if not isinstance(previous_state, dict):
            return None

        restore_actions = previous_state.get("restore_actions")
        if not isinstance(restore_actions, list) or not restore_actions:
            return None

        intents: list[Intent] = []
        for action_payload in restore_actions:
            if not isinstance(action_payload, dict):
                continue
            action = action_payload.get("action")
            action_target = action_payload.get("target")
            parameters = action_payload.get("parameters") or {}
            if not isinstance(action, str) or not isinstance(action_target, str):
                continue
            if not isinstance(parameters, dict):
                parameters = {}
            intents.append(
                Intent(
                    action=action,
                    target=action_target,
                    parameters=parameters,
                    rationale=f"Matched local restore rule for {target}.",
                )
            )

        if not intents:
            return None

        return ActionPlan(
            actions=intents,
            rationale=f"Matched local restore rule for {target}.",
            schedule=schedule,
            assistant_response=f"Done. I restored {target.split('.', 1)[1].replace('_', ' ')} to its previous state.",
        )

    def _previous_restore_action_count(self, previous_state: object) -> int:
        if not isinstance(previous_state, dict):
            return 0
        restore_actions = previous_state.get("restore_actions")
        return len(restore_actions) if isinstance(restore_actions, list) else 0

    def _latest_previous_state_target(
        self,
        targets: list[str],
        previous_states: dict,
    ) -> str | None:
        latest_target: str | None = None
        latest_captured_at = ""
        for target in targets:
            previous_state = previous_states.get(target)
            if not isinstance(previous_state, dict):
                continue
            captured_at = previous_state.get("captured_at")
            if not isinstance(captured_at, str):
                continue
            if captured_at > latest_captured_at:
                latest_captured_at = captured_at
                latest_target = target
        return latest_target

    def _find_target_by_action(
        self,
        text: str,
        target_capabilities: dict[str, TargetCapabilities],
        action: str,
        domain: str | None = None,
    ) -> str | None:
        best_target: str | None = None
        best_match_length = 0

        for target_id, capabilities in target_capabilities.items():
            if domain is not None and capabilities.domain != domain:
                continue
            if action not in capabilities.actions:
                continue

            matches = self._matching_keywords(target_id, capabilities.kind, capabilities.aliases, text)
            if not matches:
                continue

            match_length = max(len(keyword) for keyword in matches)
            if match_length > best_match_length:
                best_target = target_id
                best_match_length = match_length

        return best_target

    def _matching_keywords(
        self,
        target_id: str,
        kind: str,
        aliases: tuple[str, ...],
        text: str,
    ) -> set[str]:
        keywords = matching_keywords_for_target(target_id, aliases, kind=kind)
        normalized_keywords = {_normalize(keyword).strip() for keyword in keywords if keyword}
        return {keyword for keyword in normalized_keywords if keyword and _contains_phrase(text, keyword)}

    def _target_capabilities_from_context(
        self,
        context: ClaudeContext,
    ) -> dict[str, TargetCapabilities]:
        raw_target_capabilities = getattr(context, "target_capabilities", {})
        if raw_target_capabilities:
            target_capabilities: dict[str, TargetCapabilities] = {}
            for target_id, data in raw_target_capabilities.items():
                actions = {
                    action_name: ActionDefinition(
                        action=action_name,
                        service_domain=None,
                        service_name=None,
                    )
                    for action_name in data.get("actions", {})
                }
                target_capabilities[target_id] = TargetCapabilities(
                    target_id=target_id,
                    kind=data.get("kind", "entity"),
                    domain=data.get("domain", target_id.split(".", 1)[0]),
                    actions=actions,
                    aliases=tuple(data.get("aliases", [])),
                    security=data.get("security", "normal"),
                )
            return target_capabilities

        return build_target_capabilities_from_lists(
            allowed_entities=context.allowed_entities,
            allowed_scenes=context.allowed_scenes,
            allowed_scripts=context.allowed_scripts,
        )
