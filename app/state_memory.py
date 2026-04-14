from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from app.models import ActionPlan, Intent
from app.persistence import load_json_file_with_backup, write_json_file_atomic


logger = logging.getLogger(__name__)


class PreviousStateRecord(BaseModel):
    target: str
    domain: str
    captured_at: datetime
    state: str | None = None
    restore_actions: list[Intent] = Field(default_factory=list)


class PreviousStateMemoryService:
    def __init__(self, settings: Any, home_assistant: Any):
        self._settings = settings
        self._home_assistant = home_assistant
        self._timezone = ZoneInfo(settings.local_timezone)
        self._enabled = bool(getattr(settings, "state_memory_enabled", True))
        self._store_path = Path(settings.state_memory_store_path)
        self._lock = asyncio.Lock()
        self._records: dict[str, PreviousStateRecord] = {}

    async def start(self) -> None:
        if not self._enabled:
            logger.info("Previous state memory disabled.")
            return

        self._store_path.parent.mkdir(parents=True, exist_ok=True)
        await self._load_records()
        logger.info("Previous state memory loaded with %s record(s).", len(self._records))

    async def stop(self) -> None:
        return

    async def get_previous_states(self, target_ids: list[str] | set[str]) -> dict[str, dict[str, Any]]:
        if not self._enabled:
            return {}

        target_id_set = set(target_ids)
        async with self._lock:
            records = {
                target_id: record
                for target_id, record in self._records.items()
                if target_id in target_id_set
            }

        return {
            target_id: {
                "captured_at": record.captured_at.isoformat(),
                "state": record.state,
                "restore_actions": [action.model_dump(mode="json") for action in record.restore_actions],
            }
            for target_id, record in records.items()
        }

    async def capture_before_plan(self, plan: ActionPlan) -> None:
        if not self._enabled:
            return

        seen_targets: set[str] = set()
        records_to_save: dict[str, PreviousStateRecord] = {}

        for intent in plan.actions:
            if not self._should_capture(intent):
                continue
            if intent.target in seen_targets:
                continue
            seen_targets.add(intent.target)

            try:
                state = await self._home_assistant.get_state(intent.target)
            except Exception as exc:  # pragma: no cover - upstream/network behavior
                logger.warning("Failed to capture previous state for %s: %s", intent.target, exc)
                continue

            records = await self._build_records(intent.target, state)
            records_to_save.update(records)

        if not records_to_save:
            return

        async with self._lock:
            self._records.update(records_to_save)
            await self._save_records()

    def _should_capture(self, intent: Intent) -> bool:
        if intent.action == "get_state":
            return False

        domain = intent.target.split(".", 1)[0].lower()
        if domain in {"scene", "script", "sensor", "binary_sensor"}:
            return False

        return True

    async def _build_records(self, target: str, state_payload: dict[str, Any]) -> dict[str, PreviousStateRecord]:
        domain = target.split(".", 1)[0].lower()
        captured_at = datetime.now(self._timezone)
        records: dict[str, PreviousStateRecord] = {}

        member_entity_ids = state_payload.get("attributes", {}).get("entity_id")
        if isinstance(member_entity_ids, list) and member_entity_ids:
            group_restore_actions: list[Intent] = []
            for member_entity_id in member_entity_ids:
                if not isinstance(member_entity_id, str) or "." not in member_entity_id:
                    continue
                try:
                    member_state = await self._home_assistant.get_state(member_entity_id)
                except Exception as exc:  # pragma: no cover - upstream/network behavior
                    logger.warning("Failed to capture previous state for group member %s: %s", member_entity_id, exc)
                    continue

                member_domain = member_entity_id.split(".", 1)[0].lower()
                member_restore_actions = self._build_restore_actions(member_entity_id, member_domain, member_state)
                if member_restore_actions:
                    records[member_entity_id] = PreviousStateRecord(
                        target=member_entity_id,
                        domain=member_domain,
                        captured_at=captured_at,
                        state=str(member_state.get("state")) if member_state.get("state") is not None else None,
                        restore_actions=member_restore_actions,
                    )
                    group_restore_actions.extend(member_restore_actions)

            if group_restore_actions:
                records[target] = PreviousStateRecord(
                    target=target,
                    domain=domain,
                    captured_at=captured_at,
                    state=str(state_payload.get("state")) if state_payload.get("state") is not None else None,
                    restore_actions=group_restore_actions,
                )
                return records

        restore_actions = self._build_restore_actions(target, domain, state_payload)
        if not restore_actions:
            return records

        records[target] = PreviousStateRecord(
            target=target,
            domain=domain,
            captured_at=captured_at,
            state=str(state_payload.get("state")) if state_payload.get("state") is not None else None,
            restore_actions=restore_actions,
        )
        return records

    def _build_restore_actions(
        self,
        target: str,
        domain: str,
        state_payload: dict[str, Any],
    ) -> list[Intent]:
        state = str(state_payload.get("state", "")).lower()
        attributes = state_payload.get("attributes", {}) or {}

        if domain == "light":
            if state == "off":
                return [Intent(action="turn_off", target=target, parameters={})]

            parameters: dict[str, Any] = {}
            brightness = attributes.get("brightness")
            if isinstance(brightness, int):
                parameters["brightness"] = brightness

            rgb_color = attributes.get("rgb_color")
            if (
                isinstance(rgb_color, (list, tuple))
                and len(rgb_color) == 3
                and all(isinstance(item, int) for item in rgb_color)
            ):
                parameters["rgb_color"] = list(rgb_color)
            else:
                color_temp_kelvin = attributes.get("color_temp_kelvin")
                if isinstance(color_temp_kelvin, int):
                    parameters["color_temp_kelvin"] = color_temp_kelvin

            return [Intent(action="turn_on", target=target, parameters=parameters)]

        if domain in {"switch", "input_boolean"}:
            action = "turn_on" if state == "on" else "turn_off"
            return [Intent(action=action, target=target, parameters={})]

        if domain == "fan":
            if state == "off":
                return [Intent(action="turn_off", target=target, parameters={})]

            percentage = attributes.get("percentage")
            if isinstance(percentage, int):
                return [Intent(action="set_fan_percentage", target=target, parameters={"percentage": percentage})]
            return [Intent(action="turn_on", target=target, parameters={})]

        if domain == "lock":
            if state == "locked":
                return [Intent(action="lock", target=target, parameters={})]
            if state == "unlocked":
                return [Intent(action="unlock", target=target, parameters={})]
            return []

        if domain == "cover":
            current_position = attributes.get("current_position")
            if isinstance(current_position, int):
                return [
                    Intent(
                        action="set_cover_position",
                        target=target,
                        parameters={"position": current_position},
                    )
                ]
            if state == "open":
                return [Intent(action="open_cover", target=target, parameters={})]
            if state == "closed":
                return [Intent(action="close_cover", target=target, parameters={})]
            return []

        if domain == "climate":
            if state == "off":
                return [Intent(action="turn_off", target=target, parameters={})]

            actions: list[Intent] = []
            hvac_mode = attributes.get("hvac_mode")
            if isinstance(hvac_mode, str) and hvac_mode:
                actions.append(
                    Intent(action="set_hvac_mode", target=target, parameters={"hvac_mode": hvac_mode})
                )

            temperature = attributes.get("temperature")
            if isinstance(temperature, (int, float)):
                actions.append(
                    Intent(
                        action="set_temperature",
                        target=target,
                        parameters={"temperature": float(temperature)},
                    )
                )

            if not actions:
                actions.append(Intent(action="turn_on", target=target, parameters={}))
            return actions

        if domain == "media_player":
            if state == "off":
                return [Intent(action="turn_off", target=target, parameters={})]

            actions: list[Intent] = []
            volume_level = attributes.get("volume_level")
            if isinstance(volume_level, (int, float)):
                actions.append(
                    Intent(
                        action="set_media_volume",
                        target=target,
                        parameters={"volume_level": float(volume_level)},
                    )
                )

            if state == "playing":
                actions.append(Intent(action="media_play", target=target, parameters={}))
            elif state == "paused":
                actions.append(Intent(action="media_pause", target=target, parameters={}))
            elif state == "idle":
                actions.append(Intent(action="media_stop", target=target, parameters={}))
            else:
                actions.append(Intent(action="turn_on", target=target, parameters={}))
            return actions

        if domain == "vacuum":
            if state == "docked":
                return [Intent(action="vacuum_return_to_base", target=target, parameters={})]
            if state == "paused":
                return [Intent(action="vacuum_pause", target=target, parameters={})]
            return [Intent(action="vacuum_start", target=target, parameters={})]

        if domain in {"select", "input_select"}:
            option = state_payload.get("state")
            if isinstance(option, str) and option:
                return [Intent(action="select_option", target=target, parameters={"option": option})]
            return []

        if domain in {"number", "input_number"}:
            value = state_payload.get("state")
            try:
                numeric_value = float(value)
            except (TypeError, ValueError):
                return []
            return [Intent(action="set_value", target=target, parameters={"value": numeric_value})]

        return []

    async def _load_records(self) -> None:
        data = await asyncio.to_thread(
            load_json_file_with_backup,
            self._store_path,
            [],
            logger=logger,
            label="Previous state store",
        )
        if not isinstance(data, list):
            logger.warning("Previous state store payload was not a list. Ignoring it.")
            return

        records: dict[str, PreviousStateRecord] = {}
        for record_data in data:
            try:
                record = PreviousStateRecord.model_validate(record_data)
            except Exception as exc:
                logger.warning("Skipping invalid previous state record: %s", exc)
                continue
            records[record.target] = record
        self._records = records

    async def _save_records(self) -> None:
        payload = [record.model_dump(mode="json") for record in self._records.values()]
        await asyncio.to_thread(write_json_file_atomic, self._store_path, payload)
