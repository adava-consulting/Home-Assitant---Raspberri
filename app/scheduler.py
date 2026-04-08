from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from app.errors import BridgeError
from app.models import ActionPlan, Intent, ScheduleSpec, ScheduledJobResponse, ScheduledJobStatus


logger = logging.getLogger(__name__)


class ScheduledJob(BaseModel):
    job_id: str
    text: str
    actions: list[Intent]
    rationale: str | None = None
    schedule: ScheduleSpec
    due_at: datetime
    created_at: datetime
    status: ScheduledJobStatus = "pending"
    executed_at: datetime | None = None
    cancelled_at: datetime | None = None
    error: str | None = None


class SchedulerService:
    def __init__(self, settings: Any, home_assistant: Any, state_memory: Any | None = None):
        self._settings = settings
        self._home_assistant = home_assistant
        self._state_memory = state_memory
        self._timezone = ZoneInfo(settings.local_timezone)
        self._store_path = Path(settings.scheduler_store_path)
        self._poll_interval = max(0.5, float(settings.scheduler_poll_interval_seconds))
        self._enabled = bool(settings.scheduling_enabled)
        self._lock = asyncio.Lock()
        self._jobs: dict[str, ScheduledJob] = {}
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if not self._enabled:
            logger.info("Scheduling disabled.")
            return

        self._store_path.parent.mkdir(parents=True, exist_ok=True)
        await self._load_jobs()
        self._task = asyncio.create_task(self._run_loop(), name="scheduled-command-runner")
        logger.info("Scheduler started with %s pending job(s).", self.pending_count)

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
    def pending_count(self) -> int:
        return sum(1 for job in self._jobs.values() if job.status == "pending")

    async def list_jobs(self, status: ScheduledJobStatus | None = None) -> list[ScheduledJobResponse]:
        async with self._lock:
            jobs = list(self._jobs.values())

        if status is not None:
            jobs = [job for job in jobs if job.status == status]

        jobs.sort(key=lambda job: (job.status != "pending", job.due_at, job.created_at))
        return [ScheduledJobResponse.model_validate(job.model_dump()) for job in jobs]

    async def cancel_job(self, job_id: str) -> ScheduledJobResponse:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise BridgeError(f"Scheduled job not found: {job_id}")
            if job.status != "pending":
                raise BridgeError(f"Only pending jobs can be cancelled. Current status: {job.status}")

            job.status = "cancelled"
            job.cancelled_at = datetime.now(self._timezone)
            job.error = None
            self._jobs[job_id] = job
            await self._save_jobs()

        logger.info("Cancelled scheduled job %s", job_id)
        return ScheduledJobResponse.model_validate(job.model_dump())

    async def schedule_plan(self, text: str, plan: ActionPlan) -> str:
        if not self._enabled:
            raise BridgeError("Scheduling is disabled.")
        if plan.schedule is None:
            raise BridgeError("Cannot schedule a plan without a schedule spec.")

        now = datetime.now(self._timezone)
        due_at = self._resolve_due_at(plan.schedule, now)
        job = ScheduledJob(
            job_id=uuid4().hex,
            text=text,
            actions=plan.actions,
            rationale=plan.rationale,
            schedule=plan.schedule,
            due_at=due_at,
            created_at=now,
        )

        async with self._lock:
            self._jobs[job.job_id] = job
            await self._save_jobs()

        logger.info("Scheduled job %s for %s (%s action(s))", job.job_id, due_at.isoformat(), len(job.actions))
        return job.job_id

    async def _run_loop(self) -> None:
        while True:
            try:
                await self._run_due_jobs()
            except Exception as exc:  # pragma: no cover - defensive background protection
                logger.exception("Scheduler loop failed: %s", exc)
            await asyncio.sleep(self._poll_interval)

    async def _run_due_jobs(self) -> None:
        now = datetime.now(self._timezone)

        async with self._lock:
            due_jobs = [
                job
                for job in self._jobs.values()
                if job.status == "pending" and job.due_at <= now
            ]

        for job in due_jobs:
            if job.status != "pending":
                continue
            try:
                logger.info("Executing scheduled job %s: %s", job.job_id, job.text)
                if self._state_memory is not None:
                    await self._state_memory.capture_before_plan(
                        ActionPlan(actions=job.actions, rationale=job.rationale)
                    )
                await self._home_assistant.execute_plan(
                    ActionPlan(actions=job.actions, rationale=job.rationale)
                )
                job.status = "completed"
                job.executed_at = datetime.now(self._timezone)
                job.error = None
            except Exception as exc:  # pragma: no cover - upstream/network behavior
                job.status = "failed"
                job.executed_at = datetime.now(self._timezone)
                job.error = str(exc)
                logger.warning("Scheduled job %s failed: %s", job.job_id, exc)

        if due_jobs:
            async with self._lock:
                for job in due_jobs:
                    self._jobs[job.job_id] = job
                await self._save_jobs()

    async def _load_jobs(self) -> None:
        if not self._store_path.exists():
            return

        payload = await asyncio.to_thread(self._store_path.read_text, "utf-8")
        if not payload.strip():
            return

        data = json.loads(payload)
        self._jobs = {
            job_data["job_id"]: ScheduledJob.model_validate(job_data)
            for job_data in data
        }

    async def _save_jobs(self) -> None:
        serialized = json.dumps(
            [job.model_dump(mode="json") for job in self._jobs.values()],
            ensure_ascii=True,
            indent=2,
        )
        await asyncio.to_thread(self._store_path.write_text, serialized, "utf-8")

    def _resolve_due_at(self, schedule: ScheduleSpec, now: datetime) -> datetime:
        if schedule.type == "delay":
            return now + timedelta(seconds=schedule.delay_seconds or 0)

        execute_at = schedule.execute_at
        if execute_at is None:
            raise BridgeError("Schedule execute_at is required for absolute scheduling.")
        if execute_at.tzinfo is None:
            return execute_at.replace(tzinfo=self._timezone)
        return execute_at.astimezone(self._timezone)
