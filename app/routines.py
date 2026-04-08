from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel

from app.errors import BridgeError
from app.models import ActionPlan, Intent, RoutineResponse, RoutineSpec, RoutineStatus


logger = logging.getLogger(__name__)


class Routine(BaseModel):
    routine_id: str
    text: str
    name: str
    actions: list[Intent]
    rationale: str | None = None
    routine: RoutineSpec
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    status: RoutineStatus = "enabled"
    error: str | None = None


class RoutineService:
    def __init__(self, settings: Any, home_assistant: Any, state_memory: Any | None = None):
        self._settings = settings
        self._home_assistant = home_assistant
        self._state_memory = state_memory
        self._timezone = ZoneInfo(settings.local_timezone)
        self._store_path = Path(settings.routines_store_path)
        self._poll_interval = max(5.0, float(settings.routines_poll_interval_seconds))
        self._enabled = bool(settings.routines_enabled)
        self._lock = asyncio.Lock()
        self._routines: dict[str, Routine] = {}
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if not self._enabled:
            logger.info("Routines disabled.")
            return

        self._store_path.parent.mkdir(parents=True, exist_ok=True)
        await self._load_routines()
        await self._normalize_stored_routines()
        await self._reschedule_missed_routines()
        self._task = asyncio.create_task(self._run_loop(), name="routine-runner")
        logger.info("Routine service started with %s enabled routine(s).", self.enabled_count)

    async def stop(self) -> None:
        if self._task is None:
            return

        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    @property
    def enabled_count(self) -> int:
        return sum(1 for routine in self._routines.values() if routine.status == "enabled")

    async def create_routine(self, text: str, plan: ActionPlan) -> str:
        if not self._enabled:
            raise BridgeError("Routines are disabled.")
        if plan.routine is None:
            raise BridgeError("Cannot create a routine without a routine spec.")
        if not plan.actions:
            raise BridgeError("Cannot create a routine without actions.")

        now = datetime.now(self._timezone)
        routine = Routine(
            routine_id=uuid4().hex,
            text=text,
            name=self._routine_name(text, plan.routine),
            actions=plan.actions,
            rationale=plan.rationale,
            routine=plan.routine,
            next_run_at=self._next_run_at(plan.routine, now),
            created_at=now,
            updated_at=now,
        )

        async with self._lock:
            self._routines[routine.routine_id] = routine
            await self._save_routines()

        logger.info(
            "Created routine %s (%s) for %s with %s action(s).",
            routine.routine_id,
            routine.name,
            routine.next_run_at.isoformat() if routine.next_run_at else "unknown time",
            len(routine.actions),
        )
        return routine.routine_id

    async def list_routines(self, status: RoutineStatus | None = None) -> list[RoutineResponse]:
        async with self._lock:
            routines = list(self._routines.values())

        if status is not None:
            routines = [routine for routine in routines if routine.status == status]
        else:
            routines = [routine for routine in routines if routine.status != "deleted"]

        routines.sort(key=lambda routine: (routine.status != "enabled", routine.next_run_at or routine.updated_at))
        return [RoutineResponse.model_validate(routine.model_dump()) for routine in routines]

    async def enable_routine(self, routine_id: str) -> RoutineResponse:
        async with self._lock:
            routine = self._routines.get(routine_id)
            if routine is None or routine.status == "deleted":
                raise BridgeError(f"Routine not found: {routine_id}")
            now = datetime.now(self._timezone)
            routine.status = "enabled"
            routine.next_run_at = self._next_run_at(routine.routine, now)
            routine.updated_at = now
            routine.error = None
            self._routines[routine_id] = routine
            await self._save_routines()

        logger.info("Enabled routine %s", routine_id)
        return RoutineResponse.model_validate(routine.model_dump())

    async def disable_routine(self, routine_id: str) -> RoutineResponse:
        async with self._lock:
            routine = self._routines.get(routine_id)
            if routine is None or routine.status == "deleted":
                raise BridgeError(f"Routine not found: {routine_id}")
            routine.status = "disabled"
            routine.updated_at = datetime.now(self._timezone)
            self._routines[routine_id] = routine
            await self._save_routines()

        logger.info("Disabled routine %s", routine_id)
        return RoutineResponse.model_validate(routine.model_dump())

    async def update_routine_time(self, routine_id: str, routine_time: str) -> RoutineResponse:
        async with self._lock:
            routine = self._routines.get(routine_id)
            if routine is None or routine.status == "deleted":
                raise BridgeError(f"Routine not found: {routine_id}")

            updated_spec = RoutineSpec(
                type=routine.routine.type,
                time=routine_time,
                name=routine.routine.name,
                timezone=routine.routine.timezone,
            )
            now = datetime.now(self._timezone)
            routine.routine = updated_spec
            routine.next_run_at = self._next_run_at(updated_spec, now) if routine.status == "enabled" else None
            routine.updated_at = now
            routine.error = None
            self._routines[routine_id] = routine
            await self._save_routines()

        logger.info("Updated routine %s time to %s", routine_id, routine_time)
        return RoutineResponse.model_validate(routine.model_dump())

    async def delete_routine(self, routine_id: str) -> RoutineResponse:
        async with self._lock:
            routine = self._routines.get(routine_id)
            if routine is None or routine.status == "deleted":
                raise BridgeError(f"Routine not found: {routine_id}")
            routine.status = "deleted"
            routine.updated_at = datetime.now(self._timezone)
            routine.next_run_at = None
            self._routines[routine_id] = routine
            await self._save_routines()

        logger.info("Deleted routine %s", routine_id)
        return RoutineResponse.model_validate(routine.model_dump())

    async def _run_loop(self) -> None:
        while True:
            try:
                await self._run_due_routines()
            except Exception as exc:  # pragma: no cover - defensive background protection
                logger.exception("Routine loop failed: %s", exc)
            await asyncio.sleep(self._poll_interval)

    async def _run_due_routines(self) -> None:
        now = datetime.now(self._timezone)

        async with self._lock:
            due_routines = [
                routine
                for routine in self._routines.values()
                if routine.status == "enabled"
                and routine.next_run_at is not None
                and routine.next_run_at <= now
            ]

        for routine in due_routines:
            try:
                logger.info("Executing routine %s: %s", routine.routine_id, routine.name)
                plan = ActionPlan(actions=routine.actions, rationale=routine.rationale)
                if self._state_memory is not None:
                    await self._state_memory.capture_before_plan(plan)
                await self._home_assistant.execute_plan(plan)
                routine.last_run_at = datetime.now(self._timezone)
                routine.error = None
            except Exception as exc:  # pragma: no cover - upstream/network behavior
                routine.last_run_at = datetime.now(self._timezone)
                routine.error = str(exc)
                logger.warning("Routine %s failed: %s", routine.routine_id, exc)

            routine.next_run_at = self._next_run_at(
                routine.routine,
                (routine.last_run_at or now) + timedelta(seconds=1),
            )
            routine.updated_at = datetime.now(self._timezone)

        if due_routines:
            async with self._lock:
                for routine in due_routines:
                    self._routines[routine.routine_id] = routine
                await self._save_routines()

    async def _load_routines(self) -> None:
        if not self._store_path.exists():
            return

        payload = await asyncio.to_thread(self._store_path.read_text, "utf-8")
        if not payload.strip():
            return

        data = json.loads(payload)
        self._routines = {
            routine_data["routine_id"]: Routine.model_validate(routine_data)
            for routine_data in data
        }

    async def _save_routines(self) -> None:
        serialized = json.dumps(
            [routine.model_dump(mode="json") for routine in self._routines.values()],
            ensure_ascii=True,
            indent=2,
        )
        await asyncio.to_thread(self._store_path.write_text, serialized, "utf-8")

    async def _reschedule_missed_routines(self) -> None:
        now = datetime.now(self._timezone)
        changed = False
        for routine in self._routines.values():
            if routine.status != "enabled":
                continue
            if routine.next_run_at is None or routine.next_run_at <= now:
                routine.next_run_at = self._next_run_at(routine.routine, now)
                routine.updated_at = now
                changed = True

        if changed:
            await self._save_routines()

    async def _normalize_stored_routines(self) -> None:
        dedupe = getattr(self._home_assistant, "dedupe_group_member_intents", None)
        if dedupe is None:
            return

        changed = False
        now = datetime.now(self._timezone)
        for routine_id, routine in list(self._routines.items()):
            if routine.status == "deleted":
                continue
            try:
                deduped_actions = await dedupe(routine.actions)
            except Exception as exc:  # pragma: no cover - defensive startup cleanup
                logger.warning("Failed to normalize routine %s: %s", routine_id, exc)
                continue

            if len(deduped_actions) == len(routine.actions):
                continue
            original_count = len(routine.actions)
            routine.actions = deduped_actions
            routine.updated_at = now
            self._routines[routine_id] = routine
            changed = True
            logger.info(
                "Normalized routine %s from %s to %s action(s).",
                routine_id,
                original_count,
                len(deduped_actions),
            )

        if changed:
            await self._save_routines()

    def _next_run_at(self, spec: RoutineSpec, after: datetime) -> datetime:
        timezone = self._routine_timezone(spec)
        local_after = after.astimezone(timezone)
        hour_text, minute_text = spec.time.split(":", 1)
        run_time = time(hour=int(hour_text), minute=int(minute_text), tzinfo=timezone)
        candidate = datetime.combine(local_after.date(), run_time)
        if candidate <= local_after:
            candidate += timedelta(days=1)
        return candidate.astimezone(self._timezone)

    def _routine_timezone(self, spec: RoutineSpec) -> ZoneInfo:
        timezone_name = spec.timezone or self._settings.local_timezone
        try:
            return ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise BridgeError(f"Unsupported routine timezone: {timezone_name}") from exc

    def _routine_name(self, text: str, spec: RoutineSpec) -> str:
        if spec.name and spec.name.strip():
            return spec.name.strip()

        normalized = " ".join(text.split())
        if len(normalized) <= 80:
            return normalized
        return f"{normalized[:77].rstrip()}..."
