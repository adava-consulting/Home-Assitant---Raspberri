import unittest

from app.capabilities import should_expand_group_action
from app.home_assistant import HomeAssistantClient
from app.models import Intent


class FakeSettings:
    home_assistant_url = "http://homeassistant.local:8123"
    request_timeout_seconds = 5.0
    home_assistant_token = "test-token"


class FakeGroupAwareHomeAssistantClient(HomeAssistantClient):
    def __init__(self, states_by_id):
        super().__init__(FakeSettings())
        self.states_by_id = states_by_id
        self.executed_intents: list[Intent] = []

    async def get_state(self, entity_id: str):
        return self.states_by_id[entity_id]

    async def _execute_single_intent(self, intent: Intent) -> dict:
        self.executed_intents.append(intent)
        return {
            "service": "fake.execute",
            "target": {"entity_id": intent.target},
            "response": {"ok": True},
        }


class GroupExpansionTests(unittest.IsolatedAsyncioTestCase):
    async def test_expands_group_light_color_change_to_members(self):
        client = FakeGroupAwareHomeAssistantClient(
            {
                "light.office": {
                    "entity_id": "light.office",
                    "state": "on",
                    "attributes": {
                        "entity_id": [
                            "light.office_light_1",
                            "light.office_light_2",
                            "light.office_light_3",
                        ]
                    },
                }
            }
        )

        result = await client.execute_intent(
            Intent(
                action="turn_on",
                target="light.office",
                parameters={"rgb_color": [255, 0, 0]},
            )
        )

        self.assertEqual(result["service"], "group.expand")
        self.assertEqual(
            result["expanded_targets"],
            ["light.office_light_1", "light.office_light_2", "light.office_light_3"],
        )
        self.assertEqual(
            [intent.target for intent in client.executed_intents],
            ["light.office_light_1", "light.office_light_2", "light.office_light_3"],
        )

    async def test_keeps_simple_group_turn_off_as_single_action(self):
        client = FakeGroupAwareHomeAssistantClient(
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
                }
            }
        )

        result = await client.execute_intent(
            Intent(
                action="turn_off",
                target="light.office",
                parameters={},
            )
        )

        self.assertEqual(result["service"], "fake.execute")
        self.assertEqual(
            [intent.target for intent in client.executed_intents],
            ["light.office"],
        )

    async def test_expands_group_climate_temperature_change(self):
        client = FakeGroupAwareHomeAssistantClient(
            {
                "climate.downstairs": {
                    "entity_id": "climate.downstairs",
                    "state": "cool",
                    "attributes": {
                        "entity_id": [
                            "climate.office",
                            "climate.hall",
                        ]
                    },
                }
            }
        )

        result = await client.execute_intent(
            Intent(
                action="set_temperature",
                target="climate.downstairs",
                parameters={"temperature": 22},
            )
        )

        self.assertEqual(result["service"], "group.expand")
        self.assertEqual(
            [intent.target for intent in client.executed_intents],
            ["climate.office", "climate.hall"],
        )


class ExpansionRuleTests(unittest.TestCase):
    def test_light_color_group_changes_expand(self):
        self.assertTrue(
            should_expand_group_action(
                "light.office",
                "turn_on",
                {"rgb_color": [255, 0, 0]},
            )
        )

    def test_light_power_group_changes_do_not_expand(self):
        self.assertFalse(should_expand_group_action("light.office", "turn_off", {}))

    def test_fan_percentage_changes_expand(self):
        self.assertTrue(
            should_expand_group_action(
                "fan.downstairs",
                "set_fan_percentage",
                {"percentage": 45},
            )
        )

    def test_lock_actions_do_not_expand(self):
        self.assertFalse(should_expand_group_action("lock.front_door", "unlock", {}))
