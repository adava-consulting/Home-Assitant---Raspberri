from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
import time
from typing import Any

from pydantic import BaseModel

from app.errors import ValidationError
from app.persistence import load_json_file_with_backup, write_json_file_atomic


class AssistGuardState(BaseModel):
    last_detection_ms: int = 0
    last_detection_at: str | None = None
    last_consumed_detection_ms: int = 0
    last_consumed_at: str | None = None


class AssistGuardService:
    def __init__(self, settings: Any):
        self._enabled = bool(getattr(settings, "assist_guard_enabled", True))
        self._recent_wake_window_seconds = max(
            1.0,
            float(getattr(settings, "assist_guard_recent_wake_window_seconds", 20.0)),
        )
        self._state_path = Path(
            getattr(
                settings,
                "assist_guard_state_file",
                "/home/claude-host-home/ha-command-bridge-data/assist_guard_state.json",
            )
        )
        self._lock = asyncio.Lock()
        self._state = AssistGuardState()

    async def start(self) -> None:
        if not self._enabled:
            return

        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        await self._load_state()

    async def stop(self) -> None:
        return None

    async def validate_and_consume(self, source: str | None) -> dict[str, Any] | None:
        normalized_source = " ".join(str(source or "").split()).lower()
        if not self._enabled or not normalized_source.startswith("assist_"):
            return None

        now_ms = int(time.time() * 1000)
        max_age_ms = int(self._recent_wake_window_seconds * 1000)

        async with self._lock:
            await self._load_state()
            detection_ms = int(self._state.last_detection_ms or 0)
            consumed_ms = int(self._state.last_consumed_detection_ms or 0)

            if detection_ms <= 0:
                raise ValidationError("Ignored Assist command because there was no recent wake event.")

            age_ms = now_ms - detection_ms
            if age_ms < 0 or age_ms > max_age_ms:
                raise ValidationError("Ignored Assist command because the wake event was stale.")

            if consumed_ms >= detection_ms:
                raise ValidationError("Ignored duplicate Assist command for an already-used wake event.")

            self._state.last_consumed_detection_ms = detection_ms
            self._state.last_consumed_at = datetime.now(UTC).isoformat()
            await self._save_state()

            return {
                "assist_guard_detection_ms": detection_ms,
                "assist_guard_detection_at": self._state.last_detection_at,
                "assist_guard_detection_age_ms": age_ms,
            }

    async def get_state(self) -> dict[str, Any]:
        async with self._lock:
            await self._load_state()
            return self._state.model_dump()

    async def _load_state(self) -> None:
        data = await asyncio.to_thread(
            load_json_file_with_backup,
            self._state_path,
            {},
            label="Assist guard state",
        )
        if isinstance(data, dict):
            self._state = AssistGuardState.model_validate(data)
        else:
            self._state = AssistGuardState()

    async def _save_state(self) -> None:
        await asyncio.to_thread(
            write_json_file_atomic,
            self._state_path,
            self._state.model_dump(mode="json"),
        )
