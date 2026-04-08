from __future__ import annotations

import json

from app.config import Settings
from app.errors import UpstreamServiceError, ValidationError
from app.json_utils import parse_json_object
from app.models import ALLOWED_ACTIONS, ActionPlan, ClaudeContext


SYSTEM_PROMPT = """
You translate smart-home requests into safe JSON only.

Rules:
- Return exactly one JSON object.
- Return an object with an `actions` array. Each item must contain `action`, `target`, `parameters`, and optional `rationale`.
- Include a short top-level `assistant_response` string that naturally confirms what will happen or what happened.
- You may include an optional top-level `schedule`.
- You may include an optional top-level `routine` for recurring routines.
- You may include an optional top-level `saved_scene` for user-created reusable scenes.
- Allowed action strings globally: __ALLOWED_ACTIONS__.
- Only use targets from `target_capabilities`.
- For each chosen target, only use actions listed under that target.
- Parameters must come only from the selected target/action schema.
- If the request is ambiguous, choose get_state only when it is clearly a status question.
- If no safe action fits, return:
  {"actions":[{"action":"get_state","target":"UNSAFE","parameters":{},"rationale":"unsafe or ambiguous"}],"rationale":"unsafe or ambiguous"}
- Parameters must be JSON objects.
- Prefer the smallest safe plan. If one grouped target can satisfy the request, prefer that over many separate actions.
- If the user asks for "all lights", "every light", or whole-house lights without naming a room, target all available light groups and standalone lights needed to cover the home. Do not silently narrow it to one room.
- The prompt may include `previous_states` with compact `restore_actions` for targets that changed before.
- If the user asks to restore, revert, go back, or return to a previous state, use the matching target's `previous_states[target].restore_actions` as the basis for the new plan.
- Use `schedule` only when the user clearly asks for future execution.
- Supported schedules:
  - `{"type":"delay","delay_seconds":300}`
  - `{"type":"at","execute_at":"2026-04-03T20:30:00-03:00"}`
- For relative phrases like "in 5 minutes", prefer `type="delay"`.
- Use `routine` only when the user clearly asks to create a recurring routine, habit, or automation.
- Supported routines:
  - `{"type":"daily","time":"07:00","name":"Bedroom morning lights"}`
- For routine requests, the `actions` array is the plan that will run each time.
- Use `saved_scene` only when the user clearly asks to create or save a reusable scene, preset, or mode.
- Supported saved scenes:
  - `{"name":"Movie mode","aliases":["movie mode"]}`
- For saved scene requests, the `actions` array is the plan that will run when the saved scene is activated later. Do not execute the scene immediately.
- Do not include `schedule`, `routine`, and `saved_scene` together. Pick only one.
- Do not create recurring routines for high-security actions such as unlocking doors.
- Do not create saved scenes for high-security actions such as unlocking doors.
- Keep `assistant_response` short, clear, and user-facing. Do not mention JSON, schemas, or internal validation.
- Do not include markdown fences.
""".strip().replace("__ALLOWED_ACTIONS__", ", ".join(ALLOWED_ACTIONS))


class ClaudeInterpreter:
    def __init__(self, settings: Settings):
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:  # pragma: no cover - depends on optional runtime install
            raise UpstreamServiceError(
                "Anthropic SDK is not installed. Install requirements or use local fallback rules."
            ) from exc

        self._client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        self._model = settings.anthropic_model

    async def interpret(self, text: str, context: ClaudeContext) -> ActionPlan:
        prompt = {
            "request_text": text,
            "time_context": context.time_context,
            "allowed_entities": context.allowed_entities,
            "allowed_scenes": context.allowed_scenes,
            "allowed_scripts": context.allowed_scripts,
            "target_capabilities": context.target_capabilities,
            "previous_states": context.previous_states,
            "visible_states": [
                {
                    "entity_id": state.get("entity_id"),
                    "state": state.get("state"),
                    "friendly_name": state.get("attributes", {}).get("friendly_name"),
                }
                for state in context.states
            ],
        }

        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=300,
                temperature=0,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": json.dumps(prompt, ensure_ascii=True)}],
            )
        except Exception as exc:  # pragma: no cover - upstream SDK/network behavior
            raise UpstreamServiceError(f"Anthropic request failed: {exc}") from exc

        raw_text = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        ).strip()

        try:
            payload = parse_json_object(raw_text)
            plan = ActionPlan.model_validate(payload)
        except Exception as exc:
            raise ValidationError(f"Claude returned an invalid action plan: {raw_text}") from exc

        return plan
