import json
import tempfile
import time
import unittest
from pathlib import Path

from app.assist_guard import AssistGuardService
from app.errors import ValidationError


class FakeAssistGuardSettings:
    assist_guard_enabled = True
    assist_guard_recent_wake_window_seconds = 20.0

    def __init__(self, state_file: str):
        self.assist_guard_state_file = state_file


class AssistGuardServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        fd, path = tempfile.mkstemp(prefix="assist-guard-", suffix=".json")
        Path(path).unlink(missing_ok=True)
        self.state_path = path
        self.settings = FakeAssistGuardSettings(self.state_path)
        self.service = AssistGuardService(self.settings)
        await self.service.start()

    async def asyncTearDown(self):
        await self.service.stop()
        Path(self.state_path).unlink(missing_ok=True)

    async def test_accepts_recent_unconsumed_detection_once(self):
        detection_ms = int(time.time() * 1000)
        Path(self.state_path).write_text(
            json.dumps(
                {
                    "last_detection_ms": detection_ms,
                    "last_detection_at": "2026-04-20T00:00:00Z",
                    "last_consumed_detection_ms": 0,
                    "last_consumed_at": None,
                }
            ),
            encoding="utf-8",
        )

        details = await self.service.validate_and_consume("assist_conversation")

        self.assertEqual(details["assist_guard_detection_ms"], detection_ms)

        with self.assertRaises(ValidationError):
            await self.service.validate_and_consume("assist_conversation")

    async def test_rejects_stale_detection(self):
        stale_detection_ms = int((time.time() - 60) * 1000)
        Path(self.state_path).write_text(
            json.dumps(
                {
                    "last_detection_ms": stale_detection_ms,
                    "last_detection_at": "2026-04-20T00:00:00Z",
                    "last_consumed_detection_ms": 0,
                    "last_consumed_at": None,
                }
            ),
            encoding="utf-8",
        )

        with self.assertRaises(ValidationError):
            await self.service.validate_and_consume("assist_conversation")

    async def test_accepts_mixed_case_assist_source(self):
        detection_ms = int(time.time() * 1000)
        Path(self.state_path).write_text(
            json.dumps(
                {
                    "last_detection_ms": detection_ms,
                    "last_detection_at": "2026-04-20T00:00:00Z",
                    "last_consumed_detection_ms": 0,
                    "last_consumed_at": None,
                }
            ),
            encoding="utf-8",
        )

        details = await self.service.validate_and_consume(" Assist_Conversation ")

        self.assertEqual(details["assist_guard_detection_ms"], detection_ms)
