from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.models import ActivityEntryResponse, Intent
from app.persistence import load_json_file_with_backup, write_json_file_atomic


logger = logging.getLogger(__name__)


class ActivityLogEntry(BaseModel):
    occurred_at: datetime
    kind: str
    source: str = Field(min_length=1, max_length=80)
    text: str = Field(default="", max_length=500)
    dry_run: bool = False
    status: str
    actions: list[Intent] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class ActivityLogService:
    def __init__(self, settings: Any):
        self._enabled = bool(getattr(settings, "activity_log_enabled", True))
        self._store_path = Path(settings.activity_log_store_path)
        self._max_entries = max(1, int(getattr(settings, "activity_log_max_entries", 200)))
        self._lock = asyncio.Lock()
        self._entries: list[ActivityLogEntry] = []

    async def start(self) -> None:
        if not self._enabled:
            logger.info("Activity log disabled.")
            return

        self._store_path.parent.mkdir(parents=True, exist_ok=True)
        await self._load_entries()

    async def stop(self) -> None:
        return None

    async def list_entries(self, limit: int = 20) -> list[ActivityEntryResponse]:
        safe_limit = max(1, min(int(limit), self._max_entries))
        async with self._lock:
            entries = list(reversed(self._entries[-safe_limit:]))
        return [ActivityEntryResponse.model_validate(entry.model_dump()) for entry in entries]

    async def record(
        self,
        *,
        kind: str,
        source: str,
        text: str,
        dry_run: bool,
        status: str,
        actions: list[Intent] | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        if not self._enabled:
            return

        normalized_source = " ".join(str(source or "unknown").split()).lower()[:80] or "unknown"
        normalized_text = " ".join(str(text or "").split())[:500]
        entry = ActivityLogEntry(
            occurred_at=datetime.now(UTC),
            kind=kind,
            source=normalized_source,
            text=normalized_text,
            dry_run=bool(dry_run),
            status=status,
            actions=list(actions or []),
            details=dict(details or {}),
        )

        async with self._lock:
            self._entries.append(entry)
            if len(self._entries) > self._max_entries:
                self._entries = self._entries[-self._max_entries :]
            await self._save_entries()

    async def _load_entries(self) -> None:
        data = await asyncio.to_thread(
            load_json_file_with_backup,
            self._store_path,
            [],
            logger=logger,
            label="Activity log store",
        )
        if not isinstance(data, list):
            logger.warning("Activity log store payload was not a list. Ignoring it.")
            return

        entries: list[ActivityLogEntry] = []
        for entry_data in data:
            try:
                entries.append(ActivityLogEntry.model_validate(entry_data))
            except Exception as exc:
                logger.warning("Skipping invalid activity log entry: %s", exc)
        self._entries = entries[-self._max_entries :]

    async def _save_entries(self) -> None:
        payload = [entry.model_dump(mode="json") for entry in self._entries]
        await asyncio.to_thread(write_json_file_atomic, self._store_path, payload)
