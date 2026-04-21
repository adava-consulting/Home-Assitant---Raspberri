import json
import tempfile
import unittest
from pathlib import Path

from app.activity_log import ActivityLogService
from app.models import Intent


class FakeActivitySettings:
    activity_log_enabled = True
    activity_log_max_entries = 2

    def __init__(self, activity_log_store_path: str):
        self.activity_log_store_path = activity_log_store_path


class ActivityLogServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        fd, path = tempfile.mkstemp(prefix="activity-log-", suffix=".json")
        Path(path).unlink(missing_ok=True)
        self.store_path = path
        self.settings = FakeActivitySettings(self.store_path)
        self.activity_log = ActivityLogService(self.settings)
        await self.activity_log.start()

    async def asyncTearDown(self):
        await self.activity_log.stop()
        Path(self.store_path).unlink(missing_ok=True)

    async def test_records_and_trims_entries(self):
        await self.activity_log.record(
            kind="command",
            source="assist_conversation",
            text="turn on the studio lights",
            dry_run=False,
            status="executed",
            actions=[Intent(action="turn_on", target="light.studio", parameters={})],
        )
        await self.activity_log.record(
            kind="scheduled_job",
            source="scheduled_job",
            text="turn off the studio lights in 10 minutes",
            dry_run=False,
            status="scheduled",
            actions=[Intent(action="turn_off", target="light.studio", parameters={})],
        )
        await self.activity_log.record(
            kind="routine",
            source="routine",
            text="turn on the studio lights every day",
            dry_run=False,
            status="executed",
            actions=[Intent(action="turn_on", target="light.studio", parameters={})],
        )

        entries = await self.activity_log.list_entries(limit=10)

        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].kind, "routine")
        self.assertEqual(entries[1].kind, "scheduled_job")

        stored_entries = json.loads(Path(self.store_path).read_text("utf-8"))
        self.assertEqual(len(stored_entries), 2)

    async def test_normalizes_source_to_lowercase(self):
        await self.activity_log.record(
            kind="command",
            source=" Assist_Conversation ",
            text="turn on the studio lights",
            dry_run=False,
            status="executed",
            actions=[Intent(action="turn_on", target="light.studio", parameters={})],
        )

        entries = await self.activity_log.list_entries(limit=10)

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].source, "assist_conversation")
