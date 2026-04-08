from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.anthropic_client import ClaudeInterpreter
from app.claude_code_cli import ClaudeCodeInterpreter
from app.command_routing import extract_forced_claude_request
from app.config import Settings
from app.errors import UpstreamServiceError, ValidationError
from app.local_interpreter import LocalInterpreter


logger = logging.getLogger(__name__)


@dataclass
class InterpreterBundle:
    interpreter: Any
    name: str


class FallbackInterpreter:
    def __init__(self, primary: Any, fallback: Any, primary_name: str):
        self._primary = primary
        self._fallback = fallback
        self._primary_name = primary_name

    async def interpret(self, text: str, context: Any):
        forced_request = extract_forced_claude_request(text)
        if forced_request is not None:
            logger.info("Claude prefix detected; using %s without local fallback.", self._primary_name)
            return await self._primary.interpret(forced_request, context)

        try:
            return await self._primary.interpret(text, context)
        except UpstreamServiceError as exc:
            logger.warning("%s unavailable, using local fallback: %s", self._primary_name, exc)
            return await self._fallback.interpret(text, context)


class LocalFirstInterpreter:
    def __init__(self, local: Any, primary: Any, primary_name: str):
        self._local = local
        self._primary = primary
        self._primary_name = primary_name

    async def interpret(self, text: str, context: Any):
        forced_request = extract_forced_claude_request(text)
        if forced_request is not None:
            logger.info("Claude prefix detected; skipping local fast path and using %s.", self._primary_name)
            return await self._primary.interpret(forced_request, context)

        try:
            plan = await self._local.interpret(text, context)
            logger.info("Fast path local interpreter matched request.")
            return plan
        except ValidationError:
            logger.info("Fast path local interpreter did not match; using %s.", self._primary_name)

        try:
            return await self._primary.interpret(text, context)
        except UpstreamServiceError as exc:
            logger.warning("%s unavailable, retrying local fallback: %s", self._primary_name, exc)
            return await self._local.interpret(text, context)


class PrefixStrippingInterpreter:
    def __init__(self, interpreter: Any, interpreter_name: str):
        self._interpreter = interpreter
        self._interpreter_name = interpreter_name

    async def interpret(self, text: str, context: Any):
        forced_request = extract_forced_claude_request(text)
        if forced_request is not None:
            logger.info("Claude prefix detected; stripping prefix for %s.", self._interpreter_name)
            return await self._interpreter.interpret(forced_request, context)

        return await self._interpreter.interpret(text, context)


def build_interpreter(settings: Settings) -> InterpreterBundle:
    mode = settings.normalized_interpreter_mode
    local_rules = LocalInterpreter(settings)

    if mode == "anthropic_api":
        return InterpreterBundle(
            interpreter=PrefixStrippingInterpreter(
                interpreter=ClaudeInterpreter(settings),
                interpreter_name="anthropic_api",
            ),
            name="anthropic_api",
        )

    if mode == "claude_cli":
        primary = ClaudeCodeInterpreter(settings)
        if getattr(settings, "fast_path_local_first", True):
            return InterpreterBundle(
                interpreter=LocalFirstInterpreter(
                    local=local_rules,
                    primary=primary,
                    primary_name="claude_cli",
                ),
                name="local_first_claude_cli_fallback",
            )
        return InterpreterBundle(
            interpreter=FallbackInterpreter(
                primary=primary,
                fallback=local_rules,
                primary_name="claude_cli",
            ),
            name="claude_cli_fallback",
        )

    if mode == "auto":
        if settings.use_anthropic:
            return InterpreterBundle(
                interpreter=PrefixStrippingInterpreter(
                    interpreter=ClaudeInterpreter(settings),
                    interpreter_name="anthropic_api",
                ),
                name="anthropic_api",
            )
        primary = ClaudeCodeInterpreter(settings)
        if getattr(settings, "fast_path_local_first", True):
            return InterpreterBundle(
                interpreter=LocalFirstInterpreter(
                    local=local_rules,
                    primary=primary,
                    primary_name="claude_cli",
                ),
                name="local_first_claude_cli_fallback",
            )
        return InterpreterBundle(
            interpreter=FallbackInterpreter(
                primary=primary,
                fallback=local_rules,
                primary_name="claude_cli",
            ),
            name="claude_cli_fallback",
        )

    return InterpreterBundle(interpreter=local_rules, name="local_rules")
