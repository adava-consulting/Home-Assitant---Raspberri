import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from app.models import ActionPlan, Intent, ScheduleSpec
from app.orchestrator import CommandOrchestrator
from app.scheduler import SchedulerService


class FakeSchedulerSettings:
    local_timezone = "America/Argentina/Buenos_Aires"
    scheduling_enabled = True
    scheduler_poll_interval_seconds = 0.5
    auto_discover_entities = False
    auto_discover_domains = []
    auto_discover_include_unavailable = False
    ignored_entities = []
    allowed_scenes = []
    allowed_scripts = []
    target_overrides = {}

    def __init__(self, scheduler_store_path: str, allowed_entities: list[str] | None = None):
        self.scheduler_store_path = scheduler_store_path
        self.allowed_entities = allowed_entities or []


class RecordingHomeAssistantClient:
    def __init__(self):
        self.executed_targets: list[list[str]] = []

    async def get_states(self):
        return [
            {
                "entity_id": "light.office_light_1",
                "state": "off",
                "attributes": {"friendly_name": "Office Light 1"},
            },
            {
                "entity_id": "light.office_light_2",
                "state": "off",
                "attributes": {"friendly_name": "Office Light 2"},
            },
            {
                "entity_id": "light.office_light_3",
                "state": "off",
                "attributes": {"friendly_name": "Office Light 3"},
            },
        ]

    async def execute_plan(self, plan: ActionPlan):
        self.executed_targets.append([intent.target for intent in plan.actions])
        return [
            {"ok": True, "target": intent.target, "action": intent.action}
            for intent in plan.actions
        ]


class FixedPlanInterpreter:
    def __init__(self, plan: ActionPlan):
        self.plan = plan

    async def interpret(self, text, context):
        return self.plan


class FakeActivityLog:
    def __init__(self):
        self.entries: list[dict] = []

    async def record(self, **payload):
        self.entries.append(payload)


class SchedulerServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        fd, path = tempfile.mkstemp(prefix="scheduled-jobs-", suffix=".json")
        Path(path).unlink(missing_ok=True)
        self.store_path = path
        self.home_assistant = RecordingHomeAssistantClient()
        self.activity_log = FakeActivityLog()
        self.settings = FakeSchedulerSettings(
            scheduler_store_path=self.store_path,
            allowed_entities=[
                "light.office_light_1",
                "light.office_light_2",
                "light.office_light_3",
            ],
        )
        self.scheduler = SchedulerService(
            self.settings,
            self.home_assistant,
            activity_log=self.activity_log,
        )
        await self.scheduler.start()

    async def asyncTearDown(self):
        await self.scheduler.stop()
        Path(self.store_path).unlink(missing_ok=True)

    async def test_multiple_scheduled_jobs_complete_independently(self):
        first = ActionPlan(
            actions=[Intent(action="turn_on", target="light.office_light_1", parameters={})],
            schedule=ScheduleSpec(type="delay", delay_seconds=1),
            rationale="First light later",
        )
        second = ActionPlan(
            actions=[Intent(action="turn_on", target="light.office_light_2", parameters={})],
            schedule=ScheduleSpec(type="delay", delay_seconds=2),
            rationale="Second light later",
        )

        first_job_id = await self.scheduler.schedule_plan("turn on office light 1 in 1 second", first)
        second_job_id = await self.scheduler.schedule_plan("turn on office light 2 in 2 seconds", second)

        await asyncio.sleep(2.7)

        self.assertNotEqual(first_job_id, second_job_id)
        self.assertEqual(
            self.home_assistant.executed_targets,
            [["light.office_light_1"], ["light.office_light_2"]],
        )

        stored_jobs = json.loads(Path(self.store_path).read_text("utf-8"))
        stored_by_id = {job["job_id"]: job for job in stored_jobs}
        self.assertEqual(stored_by_id[first_job_id]["status"], "completed")
        self.assertEqual(stored_by_id[second_job_id]["status"], "completed")

    async def test_immediate_request_does_not_consume_pending_scheduled_job(self):
        delayed_plan = ActionPlan(
            actions=[Intent(action="turn_on", target="light.office_light_2", parameters={})],
            schedule=ScheduleSpec(type="delay", delay_seconds=5),
            rationale="Later action",
        )
        await self.scheduler.schedule_plan("turn on office light 2 in 5 seconds", delayed_plan)

        orchestrator = CommandOrchestrator(
            self.settings,
            self.home_assistant,
            FixedPlanInterpreter(
                ActionPlan(
                    actions=[Intent(action="turn_on", target="light.office_light_3", parameters={})],
                    rationale="Immediate action",
                )
            ),
            scheduler=self.scheduler,
        )

        response = await orchestrator.process("turn on office light 3 now", dry_run=False)

        self.assertTrue(response.executed)
        self.assertFalse(response.scheduled)
        self.assertEqual(self.home_assistant.executed_targets[-1], ["light.office_light_3"])
        self.assertEqual(self.scheduler.pending_count, 1)

    async def test_cancel_pending_job_prevents_execution(self):
        delayed_plan = ActionPlan(
            actions=[Intent(action="turn_on", target="light.office_light_1", parameters={})],
            schedule=ScheduleSpec(type="delay", delay_seconds=1),
            rationale="Cancelled later action",
        )

        job_id = await self.scheduler.schedule_plan("turn on office light 1 in 1 second", delayed_plan)
        cancelled_job = await self.scheduler.cancel_job(job_id)

        await asyncio.sleep(1.7)

        self.assertEqual(cancelled_job.status, "cancelled")
        self.assertEqual(self.home_assistant.executed_targets, [])
        self.assertEqual(self.scheduler.pending_count, 0)

    async def test_list_jobs_can_filter_pending(self):
        pending_plan = ActionPlan(
            actions=[Intent(action="turn_on", target="light.office_light_1", parameters={})],
            schedule=ScheduleSpec(type="delay", delay_seconds=5),
            rationale="Pending action",
        )
        completed_plan = ActionPlan(
            actions=[Intent(action="turn_on", target="light.office_light_2", parameters={})],
            schedule=ScheduleSpec(type="delay", delay_seconds=1),
            rationale="Completed action",
        )

        await self.scheduler.schedule_plan("turn on office light 1 in 5 seconds", pending_plan)
        await self.scheduler.schedule_plan("turn on office light 2 in 1 second", completed_plan)

        await asyncio.sleep(1.7)

        pending_jobs = await self.scheduler.list_jobs(status="pending")
        completed_jobs = await self.scheduler.list_jobs(status="completed")

        self.assertEqual(len(pending_jobs), 1)
        self.assertEqual(pending_jobs[0].text, "turn on office light 1 in 5 seconds")
        self.assertEqual(len(completed_jobs), 1)
        self.assertEqual(completed_jobs[0].text, "turn on office light 2 in 1 second")

    async def test_completed_job_is_written_to_activity_log(self):
        delayed_plan = ActionPlan(
            actions=[Intent(action="turn_on", target="light.office_light_1", parameters={})],
            schedule=ScheduleSpec(type="delay", delay_seconds=1),
            rationale="Logged scheduled action",
        )

        await self.scheduler.schedule_plan("turn on office light 1 in 1 second", delayed_plan)

        await asyncio.sleep(1.7)

        self.assertEqual(len(self.activity_log.entries), 1)
        self.assertEqual(self.activity_log.entries[0]["kind"], "scheduled_job")
        self.assertEqual(self.activity_log.entries[0]["source"], "scheduled_job")
        self.assertEqual(self.activity_log.entries[0]["status"], "executed")
