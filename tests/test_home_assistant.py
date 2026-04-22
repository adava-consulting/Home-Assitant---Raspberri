import unittest

from app.capabilities import should_expand_group_action
from app.health import build_health_payload
from app.home_assistant import HomeAssistantClient
from app.models import Intent


class FakeSettings:
    home_assistant_url = "http://homeassistant.local:8123"
    request_timeout_seconds = 5.0
    home_assistant_token = "test-token"
    home_assistant_state_cache_ttl_seconds = 0.0


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


class FakeHealthHomeAssistantClient(HomeAssistantClient):
    def __init__(self, states):
        super().__init__(FakeSettings())
        self._states = states

    async def get_states(self):
        return self._states


class FakeAudioOutput:
    def diagnostics(self) -> dict:
        return {
            "enabled": True,
            "preferred_engine": "kokoro",
            "active_engine": "piper",
            "device": "plughw:0,0",
            "cache_enabled": True,
        }


class FakeLocalScriptService:
    def __init__(self):
        self.executed_intents: list[Intent] = []

    def can_handle(self, intent: Intent) -> bool:
        return intent.target in {"script.monitor_sleep", "script.monitor_wake"}

    async def execute_intent(self, intent: Intent) -> dict:
        self.executed_intents.append(intent)
        return {
            "service": "host.monitor_control",
            "target": {"entity_id": intent.target},
            "response": {"ok": True},
        }


class FakeCounter:
    def __init__(self, count_name: str, value: int):
        setattr(self, count_name, value)


class FakeHealthSettings:
    normalized_interpreter_mode = "claude_cli"
    fast_path_local_first = True
    voice_model = {"allowed_entities": ["light.room"]}
    health_monitored_entities = ["light.room", "light.office"]


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeAsyncClient:
    def __init__(self, states_payload, state_payload_by_id):
        self.states_payload = states_payload
        self.state_payload_by_id = state_payload_by_id
        self.get_calls: list[str] = []
        self.post_calls: list[tuple[str, dict]] = []
        self.closed = False

    async def get(self, url: str):
        self.get_calls.append(url)
        if url.endswith("/api/states"):
            return FakeResponse(self.states_payload)

        entity_id = url.rsplit("/", 1)[-1]
        return FakeResponse(self.state_payload_by_id[entity_id])

    async def post(self, url: str, json: dict):
        self.post_calls.append((url, json))
        return FakeResponse([])

    async def aclose(self):
        self.closed = True


class FakeCachedSettings(FakeSettings):
    home_assistant_state_cache_ttl_seconds = 60.0


class CachedHomeAssistantClient(HomeAssistantClient):
    def __init__(self, settings, fake_client):
        super().__init__(settings)
        self.fake_client = fake_client

    async def _get_client(self):
        return self.fake_client


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

    async def test_expands_simple_group_turn_off_to_members(self):
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

        self.assertEqual(result["service"], "group.expand")
        self.assertEqual(
            [intent.target for intent in client.executed_intents],
            ["light.office_light_1", "light.office_light_2"],
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

    def test_light_power_group_changes_expand(self):
        self.assertTrue(should_expand_group_action("light.office", "turn_off", {}))

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


class ClientCachingTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_state_reuses_recent_state_snapshot(self):
        fake_client = FakeAsyncClient(
            states_payload=[
                {
                    "entity_id": "light.room",
                    "state": "on",
                    "attributes": {"friendly_name": "Room"},
                }
            ],
            state_payload_by_id={
                "light.room": {
                    "entity_id": "light.room",
                    "state": "off",
                    "attributes": {"friendly_name": "Room"},
                }
            },
        )
        client = CachedHomeAssistantClient(FakeCachedSettings(), fake_client)

        states = await client.get_states()
        state = await client.get_state("light.room")

        self.assertEqual(states[0]["state"], "on")
        self.assertEqual(state["state"], "on")
        self.assertEqual(fake_client.get_calls, ["http://homeassistant.local:8123/api/states"])

    async def test_execute_single_intent_clears_state_cache(self):
        fake_client = FakeAsyncClient(
            states_payload=[
                {
                    "entity_id": "light.room",
                    "state": "on",
                    "attributes": {"friendly_name": "Room"},
                }
            ],
            state_payload_by_id={
                "light.room": {
                    "entity_id": "light.room",
                    "state": "off",
                    "attributes": {"friendly_name": "Room"},
                }
            },
        )
        client = CachedHomeAssistantClient(FakeCachedSettings(), fake_client)

        await client.get_states()
        await client._execute_single_intent(Intent(action="turn_off", target="light.room", parameters={}))
        state = await client.get_state("light.room")

        self.assertEqual(state["state"], "off")
        self.assertEqual(
            fake_client.get_calls,
            [
                "http://homeassistant.local:8123/api/states",
                "http://homeassistant.local:8123/api/states/light.room",
            ],
        )

    async def test_execute_intent_uses_local_script_service_for_monitor_targets(self):
        fake_client = FakeAsyncClient(
            states_payload=[],
            state_payload_by_id={},
        )
        local_script_service = FakeLocalScriptService()
        client = CachedHomeAssistantClient(FakeCachedSettings(), fake_client)
        client._local_script_service = local_script_service

        result = await client.execute_intent(
            Intent(action="run_script", target="script.monitor_sleep", parameters={})
        )

        self.assertEqual(result["service"], "host.monitor_control")
        self.assertEqual(
            [intent.target for intent in local_script_service.executed_intents],
            ["script.monitor_sleep"],
        )
        self.assertEqual(fake_client.post_calls, [])


class HealthSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_build_health_snapshot_marks_missing_and_unavailable_entities(self):
        client = FakeHealthHomeAssistantClient(
            [
                {
                    "entity_id": "light.room",
                    "state": "on",
                    "attributes": {"friendly_name": "Room"},
                },
                {
                    "entity_id": "light.office",
                    "state": "unavailable",
                    "attributes": {"friendly_name": "Office"},
                },
            ]
        )

        snapshot = await client.build_health_snapshot(["light.room", "light.office", "light.kitchen"])

        self.assertTrue(snapshot["reachable"])
        self.assertEqual(snapshot["degraded_entity_count"], 2)
        self.assertEqual(
            snapshot["monitored_entities"],
            [
                {
                    "entity_id": "light.room",
                    "status": "ok",
                    "state": "on",
                    "friendly_name": "Room",
                },
                {
                    "entity_id": "light.office",
                    "status": "degraded",
                    "state": "unavailable",
                    "friendly_name": "Office",
                },
                {
                    "entity_id": "light.kitchen",
                    "status": "missing",
                },
            ],
        )

    async def test_build_health_payload_becomes_degraded_when_monitored_entity_is_unavailable(self):
        client = FakeHealthHomeAssistantClient(
            [
                {
                    "entity_id": "light.room",
                    "state": "on",
                    "attributes": {"friendly_name": "Room"},
                },
                {
                    "entity_id": "light.office",
                    "state": "unavailable",
                    "attributes": {"friendly_name": "Office"},
                },
            ]
        )

        payload = await build_health_payload(
            settings=FakeHealthSettings(),
            interpreter_name="claude_cli",
            scheduler=FakeCounter("pending_count", 2),
            routines=FakeCounter("enabled_count", 1),
            saved_scenes=FakeCounter("active_count", 3),
            home_assistant=client,
            audio_output=FakeAudioOutput(),
        )

        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["interpreter"], "claude_cli")
        self.assertEqual(payload["interpreter_mode"], "claude_cli")
        self.assertTrue(payload["fast_path_local_first"])
        self.assertEqual(payload["scheduled_jobs"], "2")
        self.assertEqual(payload["routines"], "1")
        self.assertEqual(payload["saved_scenes"], "3")
        self.assertTrue(payload["voice_model_loaded"])
        self.assertEqual(payload["audio_output"]["active_engine"], "piper")
        self.assertEqual(payload["home_assistant"]["monitored_entities"][1]["entity_id"], "light.office")
