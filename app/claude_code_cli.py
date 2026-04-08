from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil

from app.config import Settings
from app.errors import UpstreamServiceError, ValidationError
from app.json_utils import parse_json_object
from app.models import ActionPlan, ClaudeContext


logger = logging.getLogger(__name__)
WORD_RE = re.compile(r"[a-z0-9]+")
ALL_LIGHTS_RE = re.compile(
    r"\b(?:all|every)\s+(?:the\s+)?(?:house\s+|home\s+)?lights?\b"
    r"|\blights?\s+(?:in|of)\s+(?:the\s+)?(?:house|home)\b"
    r"|\btodas?\s+las?\s+luces\b"
    r"|\bluces\s+de\s+(?:la\s+)?casa\b"
)


class ClaudeCodeInterpreter:
    def __init__(self, settings: Settings):
        self._command = settings.claude_cli_command
        self._cwd = settings.claude_cli_cwd
        self._home = settings.claude_cli_home
        self._timeout = settings.claude_cli_timeout_seconds
        self._disable_auto_memory = settings.claude_cli_disable_auto_memory
        self._max_prompt_targets = max(1, settings.claude_cli_max_prompt_targets)
        self._max_visible_states = max(1, settings.claude_cli_max_visible_states)

    async def interpret(self, text: str, context: ClaudeContext) -> ActionPlan:
        if shutil.which(self._command) is None:
            raise UpstreamServiceError(f"Claude CLI command not found in PATH: {self._command}")

        prompt = self._build_prompt(text, context)
        prompt_json = json.dumps(prompt, ensure_ascii=True)

        command = [
            self._command,
            "-p",
            prompt_json,
            "--output-format",
            "json",
        ]

        env = os.environ.copy()
        if self._home:
            env["HOME"] = self._home
        if self._disable_auto_memory:
            env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
        anthropic_api_key = env.get("ANTHROPIC_API_KEY", "").strip()
        if not anthropic_api_key or anthropic_api_key.startswith("replace-with-"):
            env.pop("ANTHROPIC_API_KEY", None)

        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=self._cwd or None,
        )

        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self._timeout)
        except TimeoutError as exc:
            process.kill()
            raise UpstreamServiceError("Claude CLI timed out before returning a result.") from exc

        stdout_text = stdout.decode("utf-8", errors="replace").strip()
        stderr_text = stderr.decode("utf-8", errors="replace").strip()

        if process.returncode != 0:
            detail = stderr_text or stdout_text or f"exit code {process.returncode}"
            raise UpstreamServiceError(f"Claude CLI failed: {detail}")

        try:
            envelope = json.loads(stdout_text)
        except json.JSONDecodeError as exc:
            raise UpstreamServiceError(f"Claude CLI returned invalid JSON envelope: {stdout_text}") from exc

        if envelope.get("is_error"):
            detail = envelope.get("result") or stderr_text or stdout_text
            raise UpstreamServiceError(f"Claude CLI reported an error: {detail}")

        logger.info(
            "Claude CLI prompt footprint: targets=%s states=%s turns=%s duration_ms=%s prompt_chars=%s approx_prompt_tokens=%s",
            len(prompt["target_capabilities"]),
            len(prompt["visible_states"]),
            envelope.get("num_turns"),
            envelope.get("duration_ms"),
            len(prompt_json),
            _estimate_tokens(prompt_json),
        )

        result_text = str(envelope.get("result", "")).strip()
        if not result_text:
            raise UpstreamServiceError("Claude CLI returned an empty result.")

        logger.info(
            "Claude CLI usage report: reported_usage=%s model_usage=%s total_cost_usd=%s result_chars=%s approx_result_tokens=%s",
            _safe_json(envelope.get("usage")),
            _safe_json(envelope.get("modelUsage")),
            envelope.get("total_cost_usd"),
            len(result_text),
            _estimate_tokens(result_text),
        )

        try:
            payload = parse_json_object(result_text)
        except ValueError as exc:
            raise UpstreamServiceError(f"Claude CLI returned invalid action-plan JSON: {result_text}") from exc

        try:
            return ActionPlan.model_validate(payload)
        except Exception as exc:
            raise ValidationError(f"Claude CLI returned an invalid action plan: {exc}") from exc

    def _build_prompt(self, text: str, context: ClaudeContext) -> dict[str, object]:
        selected_targets = _select_prompt_targets(
            text=text,
            target_capabilities=context.target_capabilities,
            max_targets=self._max_prompt_targets,
        )
        selected_target_ids = set(selected_targets)
        selected_states = _select_visible_states(
            states=context.states,
            selected_target_ids=selected_target_ids,
            max_states=self._max_visible_states,
        )
        selected_previous_states = {
            target_id: context.previous_states[target_id]
            for target_id in selected_target_ids
            if target_id in context.previous_states
        }

        return {
            "request_text": text,
            "time_context": context.time_context,
            "target_capabilities": selected_targets,
            "visible_states": selected_states,
            "previous_states": selected_previous_states,
        }


def _select_prompt_targets(
    *,
    text: str,
    target_capabilities: dict[str, dict],
    max_targets: int,
) -> dict[str, dict]:
    items = list(target_capabilities.items())
    if not items:
        return {}

    scored_items = [
        (target_id, capabilities, _score_target(text, target_id, capabilities))
        for target_id, capabilities in items
    ]
    matching_items = [
        (target_id, capabilities, score)
        for target_id, capabilities, score in scored_items
        if score > 0
    ]

    if matching_items:
        ranked = sorted(
            matching_items,
            key=lambda item: (item[2], item[0]),
            reverse=True,
        )
        selected_items = [(target_id, capabilities) for target_id, capabilities, _ in ranked[:max_targets]]
    else:
        selected_items = sorted(items, key=lambda item: item[0])[:max_targets]
    return {
        target_id: _compact_target_capabilities(capabilities)
        for target_id, capabilities in selected_items
    }


def _select_visible_states(
    *,
    states: list[dict],
    selected_target_ids: set[str],
    max_states: int,
) -> list[dict[str, str | None]]:
    selected_states = [
        state
        for state in states
        if state.get("entity_id") in selected_target_ids
    ]

    if not selected_states:
        selected_states = states[:max_states]

    return [
        {
            "entity_id": state.get("entity_id"),
            "state": state.get("state"),
            "friendly_name": state.get("attributes", {}).get("friendly_name"),
        }
        for state in selected_states[:max_states]
    ]


def _compact_target_capabilities(capabilities: dict) -> dict:
    return {
        "kind": capabilities.get("kind"),
        "domain": capabilities.get("domain"),
        "aliases": capabilities.get("aliases", []),
        "security": capabilities.get("security", "normal"),
        "actions": {
            action_name: {
                "parameters": action_data.get("parameters", {}),
            }
            for action_name, action_data in capabilities.get("actions", {}).items()
        },
    }


def _score_target(text: str, target_id: str, capabilities: dict) -> int:
    normalized_text = _normalize_text(text)
    text_words = set(WORD_RE.findall(normalized_text))
    score = 0

    for alias in capabilities.get("aliases", []):
        normalized_alias = _normalize_text(alias)
        if not normalized_alias:
            continue

        if normalized_alias in normalized_text:
            score = max(score, 100 + (10 * len(normalized_alias.split())))

        alias_words = set(WORD_RE.findall(normalized_alias))
        overlap = len(alias_words & text_words)
        if overlap:
            score = max(score, overlap * 10)

    slug_words = set(WORD_RE.findall(target_id.split(".", 1)[1].replace("_", " ")))
    overlap = len(slug_words & text_words)
    if overlap:
        score += overlap * 5

    domain = str(capabilities.get("domain", "")).lower()
    if domain == "light" and ALL_LIGHTS_RE.search(normalized_text):
        score += 50
    if domain and domain in text_words:
        score += 2

    return score


def _normalize_text(value: str) -> str:
    return " ".join(WORD_RE.findall(value.lower()))


def _estimate_tokens(value: str) -> int:
    # A rough English/JSON heuristic. Claude Code subscription usage is not billed
    # exactly like API tokens, but this is useful for spotting oversized prompts.
    return max(1, round(len(value) / 4))


def _safe_json(value: object) -> str:
    if value is None:
        return "null"
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True)
    except TypeError:
        return str(value)
