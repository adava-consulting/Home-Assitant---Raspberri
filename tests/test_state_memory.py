import asyncio
import tempfile
import unittest
from pathlib import Path

from app.models import ActionPlan, Intent
from app.state_memory import PreviousStateMemoryService


class FakeStateMemorySettings:
    local_timezone = "America/Argentina/Buenos_Aires"
    state_memory_enabled = True

    def __init__(self, store_path: str):
        self.state_memory_store_path = store_path


class FakeHomeAssistantClient:
    def __init__(self, states_by_id):
        self.states_by_id = states_by_id

    async def get_state(self, entity_id: str):
        return self.states_by_id[entity_id]


class PreviousStateMemoryServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        fd, path = tempfile.mkstemp(prefix="previous-state-", suffix=".json")
        Path(path).unlink(missing_ok=True)
        self.store_path = path

    async def asyncTearDown(self):
        Path(self.store_path).unlink(missing_ok=True)

    async def test_captures_light_restore_actions(self):
        service = PreviousStateMemoryService(
            FakeStateMemorySettings(self.store_path),
            FakeHomeAssistantClient(
                {
                    "light.office": {
                        "entity_id": "light.office",
                        "state": "on",
                        "attributes": {
                            "brightness": 140,
                            "rgb_color": [255, 200, 120],
                        },
                    }
                }
            ),
        )
        await service.start()

        await service.capture_before_plan(
            ActionPlan(actions=[Intent(action="turn_on", target="light.office", parameters={"rgb_color": [0, 0, 255]})])
        )

        previous_states = await service.get_previous_states(["light.office"])
        self.assertIn("light.office", previous_states)
        restore_actions = previous_states["light.office"]["restore_actions"]
        self.assertEqual(restore_actions[0]["action"], "turn_on")
        self.assertEqual(restore_actions[0]["parameters"]["brightness"], 140)
        self.assertEqual(restore_actions[0]["parameters"]["rgb_color"], [255, 200, 120])

    async def test_captures_off_state_as_turn_off(self):
        service = PreviousStateMemoryService(
            FakeStateMemorySettings(self.store_path),
            FakeHomeAssistantClient(
                {
                    "switch.kettle": {
                        "entity_id": "switch.kettle",
                        "state": "off",
                        "attributes": {},
                    }
                }
            ),
        )
        await service.start()

        await service.capture_before_plan(
            ActionPlan(actions=[Intent(action="turn_on", target="switch.kettle", parameters={})])
        )

        previous_states = await service.get_previous_states(["switch.kettle"])
        restore_actions = previous_states["switch.kettle"]["restore_actions"]
        self.assertEqual(restore_actions, [{"action": "turn_off", "target": "switch.kettle", "parameters": {}, "rationale": None}])

    async def test_captures_group_and_member_restore_actions(self):
        service = PreviousStateMemoryService(
            FakeStateMemorySettings(self.store_path),
            FakeHomeAssistantClient(
                {
                    "light.office": {
                        "entity_id": "light.office",
                        "state": "on",
                        "attributes": {
                            "entity_id": [
                                "light.office_light_1",
                                "light.office_light_2",
                            ]
                        },
                    },
                    "light.office_light_1": {
                        "entity_id": "light.office_light_1",
                        "state": "on",
                        "attributes": {
                            "brightness": 100,
                            "rgb_color": [255, 200, 120],
                        },
                    },
                    "light.office_light_2": {
                        "entity_id": "light.office_light_2",
                        "state": "off",
                        "attributes": {},
                    },
                }
            ),
        )
        await service.start()

        await service.capture_before_plan(
            ActionPlan(actions=[Intent(action="turn_on", target="light.office", parameters={"rgb_color": [0, 0, 255]})])
        )

        previous_states = await service.get_previous_states(
            ["light.office", "light.office_light_1", "light.office_light_2"]
        )

        self.assertIn("light.office", previous_states)
        self.assertIn("light.office_light_1", previous_states)
        self.assertIn("light.office_light_2", previous_states)
        self.assertEqual(len(previous_states["light.office"]["restore_actions"]), 2)
        self.assertEqual(previous_states["light.office_light_1"]["restore_actions"][0]["action"], "turn_on")
        self.assertEqual(previous_states["light.office_light_2"]["restore_actions"][0]["action"], "turn_off")
