from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.capabilities import resolve_action_definition, should_expand_group_action
from app.config import Settings
from app.errors import UpstreamServiceError
from app.models import ActionPlan, Intent


logger = logging.getLogger(__name__)


class HomeAssistantClient:
    def __init__(self, settings: Settings):
        self._base_url = settings.home_assistant_url.rstrip("/")
        self._timeout = settings.request_timeout_seconds
        self._headers = {
            "Authorization": f"Bearer {settings.home_assistant_token}",
            "Content-Type": "application/json",
        }

    async def get_states(self) -> list[dict[str, Any]]:
        try:
            async with httpx.AsyncClient(timeout=self._timeout, headers=self._headers) as client:
                response = await client.get(f"{self._base_url}/api/states")
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as exc:
            raise UpstreamServiceError(f"Failed to fetch Home Assistant states: {exc}") from exc

    async def get_state(self, entity_id: str) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self._timeout, headers=self._headers) as client:
                response = await client.get(f"{self._base_url}/api/states/{entity_id}")
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as exc:
            raise UpstreamServiceError(f"Failed to fetch entity state for {entity_id}: {exc}") from exc

    async def get_weather_forecast(self, entity_id: str, forecast_type: str) -> list[dict[str, Any]]:
        try:
            async with httpx.AsyncClient(timeout=self._timeout, headers=self._headers) as client:
                response = await client.post(
                    f"{self._base_url}/api/services/weather/get_forecasts?return_response",
                    json={"entity_id": entity_id, "type": forecast_type},
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            raise UpstreamServiceError(
                f"Failed to fetch {forecast_type} forecast for {entity_id}: {exc}"
            ) from exc

        service_response = payload.get("service_response")
        if not isinstance(service_response, dict):
            return []

        forecast_payload = service_response.get(entity_id)
        if not isinstance(forecast_payload, dict):
            return []

        forecast = forecast_payload.get("forecast")
        return forecast if isinstance(forecast, list) else []

    async def execute_intent(self, intent: Intent) -> dict[str, Any]:
        if intent.action == "get_state":
            state = await self.get_state(intent.target)
            return {
                "service": "state.read",
                "target": {"entity_id": intent.target},
                "response": state,
            }

        expanded_intents = await self._expand_group_intent(intent)
        if len(expanded_intents) > 1:
            results: list[dict[str, Any]] = []
            for expanded_intent in expanded_intents:
                results.append(await self._execute_single_intent(expanded_intent))
            return {
                "service": "group.expand",
                "target": {"entity_id": intent.target},
                "expanded_targets": [expanded_intent.target for expanded_intent in expanded_intents],
                "response": results,
            }

        return await self._execute_single_intent(expanded_intents[0])

    async def _execute_single_intent(self, intent: Intent) -> dict[str, Any]:
        action_definition = resolve_action_definition(intent.target, intent.action)
        if action_definition is None or action_definition.service_name is None:
            raise UpstreamServiceError(
                f"No Home Assistant service mapping is configured for {intent.action} on {intent.target}."
            )

        domain = action_definition.service_domain or intent.target.split(".", 1)[0]
        service = action_definition.service_name

        payload = {"entity_id": intent.target, **intent.parameters}

        try:
            async with httpx.AsyncClient(timeout=self._timeout, headers=self._headers) as client:
                response = await client.post(
                    f"{self._base_url}/api/services/{domain}/{service}",
                    json=payload,
                )
                response.raise_for_status()
                return {
                    "service": f"{domain}.{service}",
                    "target": {"entity_id": intent.target},
                    "response": response.json(),
                }
        except httpx.HTTPError as exc:
            raise UpstreamServiceError(f"Failed to execute Home Assistant service {domain}.{service}: {exc}") from exc

    async def _expand_group_intent(self, intent: Intent) -> list[Intent]:
        if not should_expand_group_action(intent.target, intent.action, intent.parameters):
            return [intent]

        try:
            target_state = await self.get_state(intent.target)
        except UpstreamServiceError:
            return [intent]

        member_entity_ids = target_state.get("attributes", {}).get("entity_id")
        if not isinstance(member_entity_ids, list) or not member_entity_ids:
            return [intent]

        target_domain = intent.target.split(".", 1)[0]
        expanded_intents = [
            Intent(
                action=intent.action,
                target=member_entity_id,
                parameters=dict(intent.parameters),
                rationale=intent.rationale,
            )
            for member_entity_id in member_entity_ids
            if isinstance(member_entity_id, str)
            and member_entity_id.startswith(f"{target_domain}.")
        ]

        return expanded_intents or [intent]

    async def execute_plan(self, plan: ActionPlan) -> list[dict[str, Any]]:
        actions = await self.dedupe_group_member_intents(plan.actions)
        results: list[dict[str, Any]] = []
        for intent in actions:
            results.append(await self.execute_intent(intent))
        return results

    async def dedupe_group_member_intents(self, intents: list[Intent]) -> list[Intent]:
        if len(intents) < 2:
            return intents

        try:
            states = await self.get_states()
        except UpstreamServiceError:
            return intents

        states_by_entity_id = {
            state.get("entity_id"): state
            for state in states
            if isinstance(state.get("entity_id"), str)
        }

        covered_member_keys: set[tuple[str, str]] = set()
        for intent in intents:
            state = states_by_entity_id.get(intent.target)
            if not isinstance(state, dict):
                continue

            member_entity_ids = state.get("attributes", {}).get("entity_id")
            if not isinstance(member_entity_ids, list) or not member_entity_ids:
                continue

            target_domain = intent.target.split(".", 1)[0]
            intent_key = self._intent_action_key(intent)
            for member_entity_id in member_entity_ids:
                if (
                    isinstance(member_entity_id, str)
                    and member_entity_id.startswith(f"{target_domain}.")
                ):
                    covered_member_keys.add((member_entity_id, intent_key))

        if not covered_member_keys:
            return intents

        deduped_intents: list[Intent] = []
        for intent in intents:
            intent_key = self._intent_action_key(intent)
            if (intent.target, intent_key) in covered_member_keys:
                continue
            deduped_intents.append(intent)

        if len(deduped_intents) != len(intents):
            logger.info(
                "Deduped group/member action plan from %s to %s action(s).",
                len(intents),
                len(deduped_intents),
            )

        return deduped_intents or intents

    def _intent_action_key(self, intent: Intent) -> str:
        try:
            parameters = json.dumps(
                intent.parameters,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
        except TypeError:
            parameters = str(sorted(intent.parameters.items()))
        return f"{intent.action}:{parameters}"
