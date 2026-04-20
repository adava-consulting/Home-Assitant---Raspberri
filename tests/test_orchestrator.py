import asyncio
import unittest

from app.capabilities import build_target_capabilities_from_lists
from app.claude_code_cli import _select_prompt_targets, _select_visible_states
from app.errors import UpstreamServiceError, ValidationError
from app.interpreter_factory import FallbackInterpreter, LocalFirstInterpreter, build_interpreter
from app.local_interpreter import LocalInterpreter
from app.models import ActionPlan, Intent, RoutineSpec, SavedSceneResponse, SavedSceneSpec, ScheduleSpec
from app.orchestrator import CommandOrchestrator


class FakeSettings:
    interpreter_mode = "local_rules"
    normalized_interpreter_mode = "local_rules"
    use_anthropic = False
    anthropic_api_key = ""
    anthropic_model = "claude-sonnet-4-20250514"
    claude_cli_command = "claude"
    claude_cli_cwd = "/app"
    claude_cli_timeout_seconds = 45.0
    claude_cli_disable_auto_memory = True
    claude_cli_max_prompt_targets = 12
    claude_cli_max_visible_states = 8
    fast_path_local_first = True
    local_timezone = "America/Argentina/Buenos_Aires"
    scheduling_enabled = True
    scheduler_poll_interval_seconds = 1.0
    scheduler_store_path = "/tmp/test-scheduled-commands.json"
    routines_enabled = True
    routines_poll_interval_seconds = 30.0
    routines_store_path = "/tmp/test-routines.json"
    saved_scenes_enabled = True
    saved_scenes_store_path = "/tmp/test-saved-scenes.json"
    state_memory_enabled = True
    state_memory_store_path = "/tmp/test-previous-state-cache.json"
    auto_discover_entities = True
    auto_discover_domains = [
        "light",
        "switch",
        "lock",
        "cover",
        "fan",
        "climate",
        "media_player",
        "vacuum",
        "sensor",
        "binary_sensor",
    ]
    auto_discover_include_unavailable = False
    ignored_entities = []
    allowed_entities = ["light.living_room", "light.kitchen"]
    allowed_scenes = ["scene.movie_time"]
    allowed_scripts = ["script.prepare_bedtime"]
    target_overrides = {}
    audio_response_fast_ack_for_local = True
    audio_response_dedupe_window_seconds = 20.0
    audio_response_max_chars = 220
    audio_response_local_ack_mode = "descriptive"
    audio_response_fast_ack_text = "Done."


class FakeHomeAssistantClient:
    async def get_states(self):
        return [
            {
                "entity_id": "light.living_room",
                "state": "off",
                "attributes": {"friendly_name": "Living Room"},
            }
        ]

    async def get_state(self, entity_id: str):
        return {
            "entity_id": entity_id,
            "state": "on",
            "attributes": {"friendly_name": "Living Room", "brightness": 120},
        }

    async def execute_plan(self, plan: ActionPlan):
        return [
            {"ok": True, "target": intent.target, "action": intent.action}
            for intent in plan.actions
        ]


class FakeInterpreter:
    def __init__(self, plan: ActionPlan):
        self.plan = plan

    async def interpret(self, text, context):
        return self.plan


class ContextCapturingInterpreter:
    def __init__(self, plan: ActionPlan):
        self.plan = plan
        self.seen_context = None

    async def interpret(self, text, context):
        self.seen_context = context
        return self.plan


class FailingInterpreter:
    async def interpret(self, text, context):
        raise UpstreamServiceError("rate limit reached")


class CountingInterpreter:
    def __init__(self, plan: ActionPlan):
        self.plan = plan
        self.calls = 0
        self.last_text = None

    async def interpret(self, text, context):
        self.calls += 1
        self.last_text = text
        return self.plan


class FakeScheduler:
    def __init__(self):
        self.calls: list[tuple[str, ActionPlan]] = []

    async def schedule_plan(self, text: str, plan: ActionPlan) -> str:
        self.calls.append((text, plan))
        return "job-123"


class FakeRoutines:
    def __init__(self):
        self.calls: list[tuple[str, ActionPlan]] = []

    async def create_routine(self, text: str, plan: ActionPlan) -> str:
        self.calls.append((text, plan))
        return "routine-123"


class FakeSavedScenes:
    def __init__(self, matched_scene: SavedSceneResponse | None = None):
        self.calls: list[tuple[str, ActionPlan]] = []
        self.matched_scene = matched_scene

    async def create_scene(self, text: str, plan: ActionPlan) -> str:
        self.calls.append((text, plan))
        return "scene-123"

    async def match_scene_request(self, text: str) -> SavedSceneResponse | None:
        return self.matched_scene


class FakeStateMemory:
    def __init__(self, previous_states: dict | None = None):
        self.previous_states = previous_states or {}
        self.captured_plans: list[ActionPlan] = []

    async def get_previous_states(self, target_ids):
        target_set = set(target_ids)
        return {
            target_id: state
            for target_id, state in self.previous_states.items()
            if target_id in target_set
        }

    async def capture_before_plan(self, plan: ActionPlan):
        self.captured_plans.append(plan)


class FakeAudioOutput:
    def __init__(self):
        self.messages: list[str] = []

    async def enqueue(self, text: str | None):
        if text is not None:
            self.messages.append(text)


def single_action_plan(
    action: str,
    target: str,
    parameters: dict | None = None,
    rationale: str | None = None,
) -> ActionPlan:
    return ActionPlan(
        actions=[
            Intent(
                action=action,
                target=target,
                parameters=parameters or {},
                rationale=rationale,
            )
        ]
    )


class CommandOrchestratorTests(unittest.TestCase):
    def test_local_audio_ack_can_use_generic_fast_confirmation(self):
        settings = FakeSettings()
        settings.audio_response_local_ack_mode = "generic"
        audio_output = FakeAudioOutput()
        orchestrator = CommandOrchestrator(
            settings,
            FakeHomeAssistantClient(),
            FakeInterpreter(
                single_action_plan(
                    "turn_off",
                    "light.living_room",
                    rationale="Matched local entity rule.",
                )
            ),
            audio_output=audio_output,
        )

        response = asyncio.run(orchestrator.process("turn off the living room light", dry_run=False))

        self.assertTrue(response.executed)
        self.assertEqual(audio_output.messages, ["Done."])

    def test_dry_run_does_not_execute(self):
        orchestrator = CommandOrchestrator(
            FakeSettings(),
            FakeHomeAssistantClient(),
            FakeInterpreter(single_action_plan("turn_on", "light.living_room")),
            scheduler=FakeScheduler(),
        )

        response = asyncio.run(orchestrator.process("turn on the living room light", dry_run=True))

        self.assertFalse(response.executed)
        self.assertEqual(response.intent.target, "light.living_room")
        self.assertEqual(len(response.actions), 1)
        self.assertEqual(response.assistant_response, "I would turn on living room.")

    def test_rejects_repetitive_corrupted_voice_input(self):
        orchestrator = CommandOrchestrator(
            FakeSettings(),
            FakeHomeAssistantClient(),
            FakeInterpreter(single_action_plan("turn_off", "light.living_room")),
        )

        with self.assertRaises(ValidationError):
            asyncio.run(
                orchestrator.process(
                    "Turn off the lights. " * 12,
                    dry_run=False,
                )
            )

    def test_claude_prefix_skips_weather_briefing_shortcut(self):
        interpreter = CountingInterpreter(single_action_plan("turn_on", "light.living_room"))
        orchestrator = CommandOrchestrator(
            FakeSettings(),
            FakeHomeAssistantClient(),
            interpreter,
            scheduler=FakeScheduler(),
        )

        response = asyncio.run(orchestrator.process("claude good morning", dry_run=True))

        self.assertEqual(response.intent.target, "light.living_room")
        self.assertEqual(interpreter.calls, 1)
        self.assertEqual(interpreter.last_text, "claude good morning")

    def test_rejects_disallowed_entity(self):
        orchestrator = CommandOrchestrator(
            FakeSettings(),
            FakeHomeAssistantClient(),
            FakeInterpreter(single_action_plan("turn_on", "light.secret_room")),
            scheduler=FakeScheduler(),
        )

        with self.assertRaises(ValidationError):
            asyncio.run(orchestrator.process("turn on the secret room light", dry_run=False))

    def test_rejects_unsafe_marker(self):
        orchestrator = CommandOrchestrator(
            FakeSettings(),
            FakeHomeAssistantClient(),
            FakeInterpreter(single_action_plan("get_state", "UNSAFE")),
            scheduler=FakeScheduler(),
        )

        with self.assertRaises(ValidationError):
            asyncio.run(orchestrator.process("do something unusual", dry_run=False))

    def test_rejects_unsupported_parameters(self):
        orchestrator = CommandOrchestrator(
            FakeSettings(),
            FakeHomeAssistantClient(),
            FakeInterpreter(
                single_action_plan(
                    "turn_on",
                    "light.living_room",
                    parameters={"entity_id": "light.kitchen"},
                )
            ),
            scheduler=FakeScheduler(),
        )

        with self.assertRaises(ValidationError):
            asyncio.run(orchestrator.process("turn on the living room", dry_run=False))

    def test_executes_allowed_scene(self):
        orchestrator = CommandOrchestrator(
            FakeSettings(),
            FakeHomeAssistantClient(),
            FakeInterpreter(single_action_plan("activate_scene", "scene.movie_time")),
            scheduler=FakeScheduler(),
        )

        response = asyncio.run(orchestrator.process("movie mode", dry_run=False))

        self.assertTrue(response.executed)
        self.assertEqual(response.result["target"], "scene.movie_time")
        self.assertEqual(response.results[0]["target"], "scene.movie_time")
        self.assertEqual(response.assistant_response, "Done. I activated movie time.")

    def test_prefers_interpreter_assistant_response_when_present(self):
        orchestrator = CommandOrchestrator(
            FakeSettings(),
            FakeHomeAssistantClient(),
            FakeInterpreter(
                ActionPlan(
                    actions=[Intent(action="turn_on", target="light.living_room", parameters={})],
                    assistant_response="The living room light is on now.",
                )
            ),
            scheduler=FakeScheduler(),
        )

        response = asyncio.run(orchestrator.process("turn on the living room light", dry_run=False))

        self.assertEqual(response.assistant_response, "The living room light is on now.")

    def test_synthesizes_parameter_aware_light_response(self):
        orchestrator = CommandOrchestrator(
            FakeSettings(),
            FakeHomeAssistantClient(),
            FakeInterpreter(
                ActionPlan(
                    actions=[
                        Intent(
                            action="turn_on",
                            target="light.living_room",
                            parameters={"brightness_pct": 50, "color_temp_kelvin": 3000},
                        )
                    ]
                )
            ),
            scheduler=FakeScheduler(),
        )

        response = asyncio.run(orchestrator.process("make the living room warmer and dimmer", dry_run=False))

        self.assertEqual(
            response.assistant_response,
            "Done. I set living room to warm white at 50% brightness."
        )

    def test_synthesizes_schedule_response(self):
        orchestrator = CommandOrchestrator(
            FakeSettings(),
            FakeHomeAssistantClient(),
            FakeInterpreter(
                ActionPlan(
                    actions=[Intent(action="turn_on", target="light.living_room", parameters={})],
                    schedule=ScheduleSpec(type="delay", delay_seconds=300),
                )
            ),
            scheduler=FakeScheduler(),
        )

        response = asyncio.run(orchestrator.process("turn on the living room in 5 minutes", dry_run=False))

        self.assertTrue(response.scheduled)
        self.assertEqual(response.assistant_response, "Okay. I scheduled living room in 5 minutes.")

    def test_creates_routine_instead_of_executing_immediately(self):
        routines = FakeRoutines()
        orchestrator = CommandOrchestrator(
            FakeSettings(),
            FakeHomeAssistantClient(),
            FakeInterpreter(
                ActionPlan(
                    actions=[Intent(action="turn_on", target="light.living_room", parameters={})],
                    routine=RoutineSpec(type="daily", time="07:00", name="Morning living room"),
                )
            ),
            scheduler=FakeScheduler(),
            routines=routines,
        )

        response = asyncio.run(
            orchestrator.process("create a routine to turn on living room every day at 7", dry_run=False)
        )

        self.assertFalse(response.executed)
        self.assertFalse(response.scheduled)
        self.assertTrue(response.routine_created)
        self.assertEqual(response.routine_id, "routine-123")
        self.assertEqual(len(routines.calls), 1)
        self.assertEqual(response.assistant_response, "Okay. I created a routine to living room every day at 07:00.")

    def test_creates_saved_scene_instead_of_executing_immediately(self):
        saved_scenes = FakeSavedScenes()
        orchestrator = CommandOrchestrator(
            FakeSettings(),
            FakeHomeAssistantClient(),
            FakeInterpreter(
                ActionPlan(
                    actions=[
                        Intent(
                            action="turn_on",
                            target="light.living_room",
                            parameters={"brightness_pct": 40, "color_temp_kelvin": 2700},
                        )
                    ],
                    saved_scene=SavedSceneSpec(name="Movie mode", aliases=["movie mode"]),
                )
            ),
            scheduler=FakeScheduler(),
            routines=FakeRoutines(),
            saved_scenes=saved_scenes,
        )

        response = asyncio.run(
            orchestrator.process("create a scene called movie mode for dim warm living room lights", dry_run=False)
        )

        self.assertFalse(response.executed)
        self.assertFalse(response.scheduled)
        self.assertFalse(response.routine_created)
        self.assertTrue(response.saved_scene_created)
        self.assertEqual(response.saved_scene_id, "scene-123")
        self.assertEqual(len(saved_scenes.calls), 1)
        self.assertEqual(response.assistant_response, "Okay. I saved Movie mode as a scene.")

    def test_activates_saved_scene_by_voice(self):
        saved_scene = SavedSceneResponse(
            scene_id="scene-123",
            text="create movie mode",
            name="Movie mode",
            aliases=["Movie mode"],
            actions=[
                Intent(
                    action="turn_on",
                    target="light.living_room",
                    parameters={"brightness_pct": 40},
                )
            ],
            created_at="2026-04-07T12:00:00-03:00",
            updated_at="2026-04-07T12:00:00-03:00",
            status="active",
        )
        orchestrator = CommandOrchestrator(
            FakeSettings(),
            FakeHomeAssistantClient(),
            FakeInterpreter(single_action_plan("turn_on", "light.kitchen")),
            scheduler=FakeScheduler(),
            routines=FakeRoutines(),
            saved_scenes=FakeSavedScenes(matched_scene=saved_scene),
        )

        response = asyncio.run(orchestrator.process("movie mode", dry_run=False))

        self.assertTrue(response.executed)
        self.assertEqual(response.intent.target, "light.living_room")
        self.assertEqual(response.assistant_response, "Okay. I activated Movie mode.")

    def test_activate_saved_scene_by_id_executes_exact_actions(self):
        saved_scene = SavedSceneResponse(
            scene_id="scene-123",
            text="create movie mode",
            name="Movie mode",
            aliases=["Movie mode"],
            actions=[
                Intent(
                    action="turn_on",
                    target="light.living_room",
                    parameters={"brightness_pct": 40},
                )
            ],
            created_at="2026-04-07T12:00:00-03:00",
            updated_at="2026-04-07T12:00:00-03:00",
            status="active",
        )
        orchestrator = CommandOrchestrator(
            FakeSettings(),
            FakeHomeAssistantClient(),
            FakeInterpreter(single_action_plan("turn_on", "light.kitchen")),
            scheduler=FakeScheduler(),
            routines=FakeRoutines(),
            saved_scenes=FakeSavedScenes(),
        )

        response = asyncio.run(
            orchestrator.activate_saved_scene(saved_scene, text="Movie mode", dry_run=False)
        )

        self.assertTrue(response.executed)
        self.assertEqual(response.intent.target, "light.living_room")
        self.assertEqual(response.saved_scene_id, "scene-123")
        self.assertEqual(response.assistant_response, "Okay. I activated Movie mode.")

    def test_rejects_high_security_saved_scene(self):
        settings = FakeSettings()
        settings.allowed_entities = ["lock.front_door"]
        settings.target_overrides = {"lock.front_door": {"security": "high"}}
        orchestrator = CommandOrchestrator(
            settings,
            FakeHomeAssistantClient(),
            FakeInterpreter(
                ActionPlan(
                    actions=[Intent(action="unlock", target="lock.front_door", parameters={})],
                    saved_scene=SavedSceneSpec(name="Unlock door scene"),
                )
            ),
            scheduler=FakeScheduler(),
            routines=FakeRoutines(),
            saved_scenes=FakeSavedScenes(),
        )

        with self.assertRaises(ValidationError):
            asyncio.run(orchestrator.process("create a scene to unlock the door", dry_run=False))

    def test_rejects_high_security_routine(self):
        settings = FakeSettings()
        settings.allowed_entities = ["lock.front_door"]
        settings.target_overrides = {"lock.front_door": {"security": "high"}}
        orchestrator = CommandOrchestrator(
            settings,
            FakeHomeAssistantClient(),
            FakeInterpreter(
                ActionPlan(
                    actions=[Intent(action="unlock", target="lock.front_door", parameters={})],
                    routine=RoutineSpec(type="daily", time="07:00", name="Unlock front door"),
                )
            ),
            scheduler=FakeScheduler(),
            routines=FakeRoutines(),
        )

        with self.assertRaises(ValidationError):
            asyncio.run(orchestrator.process("unlock the front door every day at 7", dry_run=False))

    def test_accepts_supported_cover_action(self):
        settings = FakeSettings()
        settings.allowed_entities = ["cover.garage"]
        orchestrator = CommandOrchestrator(
            settings,
            FakeHomeAssistantClient(),
            FakeInterpreter(single_action_plan("open_cover", "cover.garage")),
            scheduler=FakeScheduler(),
        )

        response = asyncio.run(orchestrator.process("open the garage", dry_run=True))

        self.assertFalse(response.executed)
        self.assertEqual(response.intent.action, "open_cover")

    def test_rejects_action_not_supported_by_target(self):
        settings = FakeSettings()
        settings.allowed_entities = ["lock.front_door"]
        orchestrator = CommandOrchestrator(
            settings,
            FakeHomeAssistantClient(),
            FakeInterpreter(single_action_plan("turn_on", "lock.front_door")),
            scheduler=FakeScheduler(),
        )

        with self.assertRaises(ValidationError):
            asyncio.run(orchestrator.process("activate the front door", dry_run=True))

    def test_rejects_brightness_out_of_range(self):
        orchestrator = CommandOrchestrator(
            FakeSettings(),
            FakeHomeAssistantClient(),
            FakeInterpreter(
                single_action_plan(
                    "turn_on",
                    "light.living_room",
                    parameters={"brightness": 999},
                )
            ),
            scheduler=FakeScheduler(),
        )

        with self.assertRaises(ValidationError):
            asyncio.run(orchestrator.process("dim the living room", dry_run=True))

    def test_prompt_target_capabilities_include_friendly_name_aliases(self):
        orchestrator = CommandOrchestrator(
            FakeSettings(),
            FakeHomeAssistantClient(),
            FakeInterpreter(single_action_plan("turn_on", "light.living_room")),
            scheduler=FakeScheduler(),
        )

        prompt_target_capabilities = orchestrator._build_prompt_target_capabilities(
            [
                {
                    "entity_id": "light.living_room",
                    "state": "off",
                    "attributes": {"friendly_name": "Living Room Lamp"},
                }
            ],
            {
                "light.living_room": build_target_capabilities_from_lists(
                    allowed_entities=["light.living_room"],
                    allowed_scenes=[],
                    allowed_scripts=[],
                )["light.living_room"]
            },
        )

        aliases = prompt_target_capabilities["light.living_room"]["aliases"]
        self.assertIn("Living Room Lamp", aliases)
        self.assertIn("living room lamp", aliases)

    def test_auto_discovers_new_supported_entity_without_env_edit(self):
        settings = FakeSettings()
        settings.allowed_entities = []

        class AutoDiscoveringHomeAssistantClient(FakeHomeAssistantClient):
            async def get_states(self):
                return [
                    {
                        "entity_id": "light.new_room_light",
                        "state": "off",
                        "attributes": {"friendly_name": "New Room Light"},
                    }
                ]

        orchestrator = CommandOrchestrator(
            settings,
            AutoDiscoveringHomeAssistantClient(),
            FakeInterpreter(single_action_plan("turn_on", "light.new_room_light")),
            scheduler=FakeScheduler(),
        )

        response = asyncio.run(orchestrator.process("turn on the new room light", dry_run=True))

        self.assertEqual(response.intent.target, "light.new_room_light")

    def test_auto_discovery_skips_unavailable_entities_by_default(self):
        settings = FakeSettings()
        settings.allowed_entities = []

        class UnavailableHomeAssistantClient(FakeHomeAssistantClient):
            async def get_states(self):
                return [
                    {
                        "entity_id": "light.bad_light",
                        "state": "unavailable",
                        "attributes": {"friendly_name": "Bad Light"},
                    }
                ]

        orchestrator = CommandOrchestrator(
            settings,
            UnavailableHomeAssistantClient(),
            FakeInterpreter(single_action_plan("turn_on", "light.bad_light")),
            scheduler=FakeScheduler(),
        )

        with self.assertRaises(ValidationError):
            asyncio.run(orchestrator.process("turn on the bad light", dry_run=True))

    def test_effective_target_capabilities_filter_out_unavailable_allowed_entities(self):
        settings = FakeSettings()
        settings.allowed_entities = ["light.room", "light.studio"]
        orchestrator = CommandOrchestrator(
            settings,
            FakeHomeAssistantClient(),
            FakeInterpreter(single_action_plan("turn_on", "light.studio")),
            scheduler=FakeScheduler(),
        )

        target_capabilities = orchestrator._build_effective_target_capabilities(
            [
                {
                    "entity_id": "light.room",
                    "state": "unavailable",
                    "attributes": {"friendly_name": "Room"},
                },
                {
                    "entity_id": "light.studio",
                    "state": "on",
                    "attributes": {"friendly_name": "Studio"},
                },
            ]
        )

        self.assertNotIn("light.room", target_capabilities)
        self.assertIn("light.studio", target_capabilities)

    def test_local_interpreter_prefers_available_entity_over_missing_alias_match(self):
        interpreter = LocalInterpreter(FakeSettings())
        target_capabilities = build_target_capabilities_from_lists(
            allowed_entities=["light.room", "light.real_room"],
            allowed_scenes=[],
            allowed_scripts=[],
            target_overrides={
                "light.room": {"aliases": ["room lights"], "actions": ["turn_on", "turn_off", "get_state"]},
                "light.real_room": {"aliases": ["room lights"], "actions": ["turn_on", "turn_off", "get_state"]},
            },
        )
        context = type(
            "Context",
            (),
            {
                "allowed_entities": ["light.room", "light.real_room"],
                "allowed_scenes": [],
                "allowed_scripts": [],
                "states": [
                    {
                        "entity_id": "light.real_room",
                        "state": "off",
                        "attributes": {"friendly_name": "Room"},
                    }
                ],
                "target_capabilities": {
                    target_id: capabilities.to_prompt_dict()
                    for target_id, capabilities in target_capabilities.items()
                },
            },
        )()

        plan = asyncio.run(interpreter.interpret("turn the room lights on", context))

        self.assertEqual(plan.primary_intent.target, "light.real_room")

    def test_local_interpreter_prefers_light_room_for_room_aliases(self):
        interpreter = LocalInterpreter(FakeSettings())
        target_capabilities = build_target_capabilities_from_lists(
            allowed_entities=["light.cuarto", "light.room", "light.studio"],
            allowed_scenes=[],
            allowed_scripts=[],
            target_overrides={
                "light.cuarto": {
                    "aliases": ["cuarto lights"],
                    "actions": ["turn_on", "turn_off", "get_state"],
                },
                "light.room": {
                    "aliases": ["room lights", "bedroom lights"],
                    "actions": ["turn_on", "turn_off", "get_state"],
                },
                "light.studio": {
                    "aliases": ["studio lights"],
                    "actions": ["turn_on", "turn_off", "get_state"],
                },
            },
        )
        context = type(
            "Context",
            (),
            {
                "allowed_entities": ["light.cuarto", "light.room", "light.studio"],
                "allowed_scenes": [],
                "allowed_scripts": [],
                "states": [
                    {
                        "entity_id": "light.cuarto",
                        "state": "off",
                        "attributes": {"friendly_name": "Cuarto"},
                    },
                    {
                        "entity_id": "light.room",
                        "state": "off",
                        "attributes": {"friendly_name": "Room"},
                    },
                ],
                "target_capabilities": {
                    target_id: capabilities.to_prompt_dict()
                    for target_id, capabilities in target_capabilities.items()
                },
            },
        )()

        plan = asyncio.run(interpreter.interpret("turn the room lights on", context))

        self.assertEqual(plan.primary_intent.target, "light.room")

    def test_local_interpreter_routes_cuarto_aliases_to_light_cuarto(self):
        interpreter = LocalInterpreter(FakeSettings())
        target_capabilities = build_target_capabilities_from_lists(
            allowed_entities=["light.cuarto", "light.room"],
            allowed_scenes=[],
            allowed_scripts=[],
            target_overrides={
                "light.cuarto": {
                    "aliases": ["cuarto lights"],
                    "actions": ["turn_on", "turn_off", "get_state"],
                },
                "light.room": {
                    "aliases": ["room lights", "bedroom lights"],
                    "actions": ["turn_on", "turn_off", "get_state"],
                },
            },
        )
        context = type(
            "Context",
            (),
            {
                "allowed_entities": ["light.cuarto", "light.room"],
                "allowed_scenes": [],
                "allowed_scripts": [],
                "states": [
                    {
                        "entity_id": "light.cuarto",
                        "state": "off",
                        "attributes": {"friendly_name": "Cuarto"},
                    },
                    {
                        "entity_id": "light.room",
                        "state": "off",
                        "attributes": {"friendly_name": "Room"},
                    },
                ],
                "target_capabilities": {
                    target_id: capabilities.to_prompt_dict()
                    for target_id, capabilities in target_capabilities.items()
                },
            },
        )()

        plan = asyncio.run(interpreter.interpret("turn the cuarto lights on", context))

        self.assertEqual(plan.primary_intent.target, "light.cuarto")

    def test_local_interpreter_prefers_last_conflicting_action_for_room_light(self):
        interpreter = LocalInterpreter(FakeSettings())
        target_capabilities = build_target_capabilities_from_lists(
            allowed_entities=["light.room"],
            allowed_scenes=[],
            allowed_scripts=[],
            target_overrides={
                "light.room": {
                    "aliases": ["room lights", "bedroom lights"],
                    "actions": ["turn_on", "turn_off", "get_state"],
                },
            },
        )
        context = type(
            "Context",
            (),
            {
                "allowed_entities": ["light.room"],
                "allowed_scenes": [],
                "allowed_scripts": [],
                "states": [
                    {
                        "entity_id": "light.room",
                        "state": "off",
                        "attributes": {"friendly_name": "Room"},
                    },
                ],
                "target_capabilities": {
                    target_id: capabilities.to_prompt_dict()
                    for target_id, capabilities in target_capabilities.items()
                },
            },
        )()

        plan = asyncio.run(
            interpreter.interpret(
                "turn off the room lights on turn on the room lights",
                context,
            )
        )

        self.assertEqual(plan.primary_intent.action, "turn_on")
        self.assertEqual(plan.primary_intent.target, "light.room")

    def test_context_includes_previous_states(self):
        interpreter = ContextCapturingInterpreter(single_action_plan("turn_on", "light.living_room"))
        state_memory = FakeStateMemory(
            previous_states={
                "light.living_room": {
                    "captured_at": "2026-04-03T20:00:00-03:00",
                    "state": "on",
                    "restore_actions": [
                        {
                            "action": "turn_on",
                            "target": "light.living_room",
                            "parameters": {"brightness": 128},
                        }
                    ],
                }
            }
        )

        orchestrator = CommandOrchestrator(
            FakeSettings(),
            FakeHomeAssistantClient(),
            interpreter,
            scheduler=FakeScheduler(),
            state_memory=state_memory,
        )

        asyncio.run(orchestrator.process("restore the previous living room light state", dry_run=True))

        self.assertIsNotNone(interpreter.seen_context)
        self.assertIn("light.living_room", interpreter.seen_context.previous_states)

    def test_immediate_execution_captures_previous_state(self):
        state_memory = FakeStateMemory()
        orchestrator = CommandOrchestrator(
            FakeSettings(),
            FakeHomeAssistantClient(),
            FakeInterpreter(single_action_plan("turn_on", "light.living_room")),
            scheduler=FakeScheduler(),
            state_memory=state_memory,
        )

        response = asyncio.run(orchestrator.process("turn on the living room light", dry_run=False))

        self.assertTrue(response.executed)
        self.assertEqual(len(state_memory.captured_plans), 1)
        self.assertEqual(state_memory.captured_plans[0].primary_intent.target, "light.living_room")

    def test_immediate_execution_enqueues_audio_response(self):
        audio_output = FakeAudioOutput()
        orchestrator = CommandOrchestrator(
            FakeSettings(),
            FakeHomeAssistantClient(),
            FakeInterpreter(
                ActionPlan(
                    actions=[Intent(action="turn_on", target="light.living_room", parameters={})],
                    assistant_response="The living room light is on now.",
                )
            ),
            scheduler=FakeScheduler(),
            audio_output=audio_output,
        )

        response = asyncio.run(orchestrator.process("turn on the living room light", dry_run=False))

        self.assertTrue(response.executed)
        self.assertEqual(audio_output.messages, ["The living room light is on now."])

    def test_dry_run_does_not_enqueue_audio_response(self):
        audio_output = FakeAudioOutput()
        orchestrator = CommandOrchestrator(
            FakeSettings(),
            FakeHomeAssistantClient(),
            FakeInterpreter(
                ActionPlan(
                    actions=[Intent(action="turn_on", target="light.living_room", parameters={})],
                    assistant_response="The living room light is on now.",
                )
            ),
            scheduler=FakeScheduler(),
            audio_output=audio_output,
        )

        response = asyncio.run(orchestrator.process("turn on the living room light", dry_run=True))

        self.assertFalse(response.executed)
        self.assertEqual(audio_output.messages, [])


class LocalInterpreterTests(unittest.TestCase):
    def test_matches_turn_on_entity(self):
        interpreter = LocalInterpreter(FakeSettings())
        context = type(
            "Context",
            (),
            {
                "allowed_entities": [
                    "input_boolean.virtual_living_room_light",
                    "input_boolean.virtual_kitchen_light",
                ],
                "allowed_scenes": [],
                "allowed_scripts": [],
            },
        )()

        intent = asyncio.run(interpreter.interpret("turn on the living room light", context))

        self.assertEqual(intent.primary_intent.action, "turn_on")
        self.assertEqual(intent.primary_intent.target, "input_boolean.virtual_living_room_light")

    def test_matches_scene(self):
        interpreter = LocalInterpreter(FakeSettings())
        context = type(
            "Context",
            (),
            {
                "allowed_entities": [],
                "allowed_scenes": ["scene.guest_mode"],
                "allowed_scripts": [],
            },
        )()

        intent = asyncio.run(interpreter.interpret("activate guest mode", context))

        self.assertEqual(intent.primary_intent.action, "activate_scene")
        self.assertEqual(intent.primary_intent.target, "scene.guest_mode")

    def test_matches_script(self):
        interpreter = LocalInterpreter(FakeSettings())
        context = type(
            "Context",
            (),
            {
                "allowed_entities": [],
                "allowed_scenes": [],
                "allowed_scripts": ["script.prepare_bedtime"],
            },
        )()

        intent = asyncio.run(interpreter.interpret("prepare the house for bedtime", context))

        self.assertEqual(intent.primary_intent.action, "run_script")
        self.assertEqual(intent.primary_intent.target, "script.prepare_bedtime")

    def test_rejects_unknown_phrase(self):
        interpreter = LocalInterpreter(FakeSettings())
        context = type(
            "Context",
            (),
            {
                "allowed_entities": ["input_boolean.virtual_living_room_light"],
                "allowed_scenes": [],
                "allowed_scripts": [],
            },
        )()

        with self.assertRaises(ValidationError):
            asyncio.run(interpreter.interpret("do something magical", context))

    def test_matches_real_tuya_light_alias(self):
        interpreter = LocalInterpreter(FakeSettings())
        context = type(
            "Context",
            (),
            {
                "allowed_entities": ["light.office_light_2"],
                "allowed_scenes": [],
                "allowed_scripts": [],
            },
        )()

        intent = asyncio.run(interpreter.interpret("turn off office light 2", context))

        self.assertEqual(intent.primary_intent.action, "turn_off")
        self.assertEqual(intent.primary_intent.target, "light.office_light_2")

    def test_matches_custom_magic_word_script(self):
        interpreter = LocalInterpreter(FakeSettings())
        target_capabilities = build_target_capabilities_from_lists(
            allowed_entities=[],
            allowed_scenes=[],
            allowed_scripts=["script.alfajor_office_toggle"],
            target_overrides={
                "script.alfajor_office_toggle": {
                    "aliases": ["alfajor"],
                }
            },
        )
        context = type(
            "Context",
            (),
            {
                "allowed_entities": [],
                "allowed_scenes": [],
                "allowed_scripts": ["script.alfajor_office_toggle"],
                "target_capabilities": {
                    target_id: capabilities.to_prompt_dict()
                    for target_id, capabilities in target_capabilities.items()
                },
            },
        )()

        intent = asyncio.run(interpreter.interpret("alfajor", context))

        self.assertEqual(intent.primary_intent.action, "run_script")
        self.assertEqual(intent.primary_intent.target, "script.alfajor_office_toggle")

    def test_does_not_match_generic_office_phrase_to_magic_script(self):
        interpreter = LocalInterpreter(FakeSettings())
        target_capabilities = build_target_capabilities_from_lists(
            allowed_entities=["light.office"],
            allowed_scenes=[],
            allowed_scripts=["script.alfajor_office_toggle"],
        )
        context = type(
            "Context",
            (),
            {
                "allowed_entities": ["light.office"],
                "allowed_scenes": [],
                "allowed_scripts": ["script.alfajor_office_toggle"],
                "target_capabilities": {
                    target_id: capabilities.to_prompt_dict()
                    for target_id, capabilities in target_capabilities.items()
                },
            },
        )()

        intent = asyncio.run(interpreter.interpret("turn off the office lights", context))

        self.assertEqual(intent.primary_intent.action, "turn_off")
        self.assertEqual(intent.primary_intent.target, "light.office")

    def test_uses_target_override_aliases(self):
        interpreter = LocalInterpreter(FakeSettings())
        target_capabilities = build_target_capabilities_from_lists(
            allowed_entities=["switch.cafetera"],
            allowed_scenes=[],
            allowed_scripts=[],
            target_overrides={
                "switch.cafetera": {
                    "aliases": ["cafetera grande"],
                    "actions": ["turn_on", "turn_off", "get_state"],
                }
            },
        )
        context = type(
            "Context",
            (),
            {
                "allowed_entities": ["switch.cafetera"],
                "allowed_scenes": [],
                "allowed_scripts": [],
                "target_capabilities": {
                    target_id: capabilities.to_prompt_dict()
                    for target_id, capabilities in target_capabilities.items()
                },
            },
        )()

        intent = asyncio.run(interpreter.interpret("turn off the large coffee machine", context))

        self.assertEqual(intent.primary_intent.action, "turn_off")
        self.assertEqual(intent.primary_intent.target, "switch.cafetera")

    def test_local_light_color_rule_is_portable_to_new_room_names(self):
        interpreter = LocalInterpreter(FakeSettings())
        target_capabilities = build_target_capabilities_from_lists(
            allowed_entities=["light.den"],
            allowed_scenes=[],
            allowed_scripts=[],
            target_overrides={
                "light.den": {
                    "aliases": ["den", "den lights"],
                    "actions": ["turn_on", "turn_off", "get_state"],
                }
            },
        )
        context = type(
            "Context",
            (),
            {
                "allowed_entities": ["light.den"],
                "allowed_scenes": [],
                "allowed_scripts": [],
                "target_capabilities": {
                    target_id: capabilities.to_prompt_dict()
                    for target_id, capabilities in target_capabilities.items()
                },
            },
        )()

        plan = asyncio.run(interpreter.interpret("change the den lights to blue", context))

        self.assertEqual(plan.primary_intent.action, "turn_on")
        self.assertEqual(plan.primary_intent.target, "light.den")
        self.assertEqual(plan.primary_intent.parameters, {"rgb_color": [0, 0, 255]})

    def test_restore_previous_state_uses_dynamic_room_aliases(self):
        interpreter = LocalInterpreter(FakeSettings())
        target_capabilities = build_target_capabilities_from_lists(
            allowed_entities=["light.den"],
            allowed_scenes=[],
            allowed_scripts=[],
            target_overrides={
                "light.den": {
                    "aliases": ["den", "den lights"],
                    "actions": ["turn_on", "turn_off", "get_state"],
                }
            },
        )
        context = type(
            "Context",
            (),
            {
                "allowed_entities": ["light.den"],
                "allowed_scenes": [],
                "allowed_scripts": [],
                "target_capabilities": {
                    target_id: capabilities.to_prompt_dict()
                    for target_id, capabilities in target_capabilities.items()
                },
                "previous_states": {
                    "light.den": {
                        "captured_at": "2026-04-07T10:00:00-03:00",
                        "restore_actions": [
                            {
                                "action": "turn_on",
                                "target": "light.den",
                                "parameters": {"brightness": 128, "rgb_color": [255, 200, 120]},
                            }
                        ],
                    }
                },
            },
        )()

        plan = asyncio.run(interpreter.interpret("restore the den lights to their previous state", context))

        self.assertEqual(plan.primary_intent.action, "turn_on")
        self.assertEqual(plan.primary_intent.target, "light.den")
        self.assertEqual(plan.primary_intent.parameters["brightness"], 128)

    def test_generic_restore_uses_latest_previous_group_without_room_hardcoding(self):
        interpreter = LocalInterpreter(FakeSettings())
        target_capabilities = build_target_capabilities_from_lists(
            allowed_entities=["light.den", "light.garage"],
            allowed_scenes=[],
            allowed_scripts=[],
            target_overrides={
                "light.den": {"aliases": ["den lights"], "actions": ["turn_on", "turn_off", "get_state"]},
                "light.garage": {"aliases": ["garage lights"], "actions": ["turn_on", "turn_off", "get_state"]},
            },
        )
        context = type(
            "Context",
            (),
            {
                "allowed_entities": ["light.den", "light.garage"],
                "allowed_scenes": [],
                "allowed_scripts": [],
                "target_capabilities": {
                    target_id: capabilities.to_prompt_dict()
                    for target_id, capabilities in target_capabilities.items()
                },
                "previous_states": {
                    "light.den": {
                        "captured_at": "2026-04-07T10:00:00-03:00",
                        "restore_actions": [
                            {"action": "turn_on", "target": "light.den_main", "parameters": {}},
                            {"action": "turn_on", "target": "light.den_corner", "parameters": {}},
                        ],
                    },
                    "light.garage": {
                        "captured_at": "2026-04-07T10:05:00-03:00",
                        "restore_actions": [
                            {"action": "turn_on", "target": "light.garage_main", "parameters": {}},
                            {"action": "turn_on", "target": "light.garage_side", "parameters": {}},
                        ],
                    },
                },
            },
        )()

        plan = asyncio.run(interpreter.interpret("return the lights to their previous state", context))

        self.assertEqual(
            [intent.target for intent in plan.actions],
            ["light.garage_main", "light.garage_side"],
        )


class InterpreterFactoryTests(unittest.TestCase):
    def test_fallback_interpreter_uses_local_rules(self):
        fallback = FallbackInterpreter(
            primary=FailingInterpreter(),
            fallback=LocalInterpreter(FakeSettings()),
            primary_name="claude_cli",
        )
        context = type(
            "Context",
            (),
            {
                "allowed_entities": ["input_boolean.virtual_living_room_light"],
                "allowed_scenes": [],
                "allowed_scripts": [],
            },
        )()

        intent = asyncio.run(fallback.interpret("turn on the living room light", context))

        self.assertEqual(intent.primary_intent.action, "turn_on")
        self.assertEqual(intent.primary_intent.target, "input_boolean.virtual_living_room_light")

    def test_command_response_keeps_legacy_fields_for_multi_action(self):
        response = asyncio.run(
            CommandOrchestrator(
                FakeSettings(),
                FakeHomeAssistantClient(),
                FakeInterpreter(
                    ActionPlan(
                        actions=[
                            Intent(action="turn_on", target="light.living_room", parameters={}),
                            Intent(action="turn_off", target="light.kitchen", parameters={}),
                        ],
                        rationale="Balance two lights",
                    )
                ),
                scheduler=FakeScheduler(),
            ).process("balance lights", dry_run=False)
        )

        self.assertEqual(len(response.actions), 2)
        self.assertEqual(response.intent.target, "light.living_room")
        self.assertEqual(response.result["steps"], 2)
        self.assertEqual(response.rationale, "Balance two lights")

    def test_rejects_unsupported_second_action_in_plan(self):
        settings = FakeSettings()
        settings.allowed_entities = ["light.living_room", "lock.front_door"]
        orchestrator = CommandOrchestrator(
            settings,
            FakeHomeAssistantClient(),
            FakeInterpreter(
                ActionPlan(
                    actions=[
                        Intent(action="turn_on", target="light.living_room", parameters={}),
                        Intent(action="turn_on", target="lock.front_door", parameters={}),
                    ]
                )
            ),
            scheduler=FakeScheduler(),
        )

        with self.assertRaises(ValidationError):
            asyncio.run(orchestrator.process("turn on the room and the front door", dry_run=True))

    def test_schedules_plan_instead_of_executing_immediately(self):
        scheduler = FakeScheduler()
        orchestrator = CommandOrchestrator(
            FakeSettings(),
            FakeHomeAssistantClient(),
            FakeInterpreter(
                ActionPlan(
                    actions=[Intent(action="turn_on", target="light.living_room", parameters={})],
                    schedule=ScheduleSpec(type="delay", delay_seconds=300),
                    rationale="Arrive soon",
                )
            ),
            scheduler=scheduler,
        )

        response = asyncio.run(orchestrator.process("turn on the living room in 5 minutes", dry_run=False))

        self.assertFalse(response.executed)
        self.assertTrue(response.scheduled)
        self.assertEqual(response.scheduled_job_id, "job-123")
        self.assertEqual(len(scheduler.calls), 1)

    def test_local_interpreter_parses_relative_schedule(self):
        interpreter = LocalInterpreter(FakeSettings())
        context = type(
            "Context",
            (),
            {
                "allowed_entities": ["light.office"],
                "allowed_scenes": [],
                "allowed_scripts": [],
            },
        )()

        plan = asyncio.run(interpreter.interpret("turn off the office lights in 5 minutes", context))

        self.assertEqual(plan.primary_intent.target, "light.office")
        self.assertIsNotNone(plan.schedule)
        self.assertEqual(plan.schedule.type, "delay")
        self.assertEqual(plan.schedule.delay_seconds, 300)

    def test_local_interpreter_parses_explicit_light_color(self):
        interpreter = LocalInterpreter(FakeSettings())
        target_capabilities = build_target_capabilities_from_lists(
            allowed_entities=["light.office"],
            allowed_scenes=[],
            allowed_scripts=[],
        )
        context = type(
            "Context",
            (),
            {
                "allowed_entities": ["light.office"],
                "allowed_scenes": [],
                "allowed_scripts": [],
                "target_capabilities": {
                    target_id: capabilities.to_prompt_dict()
                    for target_id, capabilities in target_capabilities.items()
                },
            },
        )()

        plan = asyncio.run(interpreter.interpret("change the office lights to red", context))

        self.assertEqual(plan.primary_intent.action, "turn_on")
        self.assertEqual(plan.primary_intent.target, "light.office")
        self.assertEqual(plan.primary_intent.parameters, {"rgb_color": [255, 0, 0]})

    def test_local_interpreter_routes_generic_lights_request_to_all_home_lights(self):
        interpreter = LocalInterpreter(FakeSettings())
        target_capabilities = build_target_capabilities_from_lists(
            allowed_entities=["light.room", "light.studio"],
            allowed_scenes=[],
            allowed_scripts=[],
            target_overrides={
                "light.room": {"aliases": ["room lights"], "actions": ["turn_on", "turn_off", "get_state"]},
                "light.studio": {"aliases": ["studio lights"], "actions": ["turn_on", "turn_off", "get_state"]},
            },
        )
        context = type(
            "Context",
            (),
            {
                "allowed_entities": ["light.room", "light.studio"],
                "allowed_scenes": [],
                "allowed_scripts": [],
                "target_capabilities": {
                    target_id: capabilities.to_prompt_dict()
                    for target_id, capabilities in target_capabilities.items()
                },
            },
        )()

        plan = asyncio.run(interpreter.interpret("turn off the lights", context))

        self.assertEqual([intent.target for intent in plan.actions], ["light.room", "light.studio"])
        self.assertTrue(all(intent.action == "turn_off" for intent in plan.actions))

    def test_local_interpreter_routes_turn_all_lights_off_phrase_to_all_home_lights(self):
        interpreter = LocalInterpreter(FakeSettings())
        target_capabilities = build_target_capabilities_from_lists(
            allowed_entities=["light.room", "light.studio"],
            allowed_scenes=[],
            allowed_scripts=[],
            target_overrides={
                "light.room": {"aliases": ["room lights"], "actions": ["turn_on", "turn_off", "get_state"]},
                "light.studio": {"aliases": ["studio lights"], "actions": ["turn_on", "turn_off", "get_state"]},
            },
        )
        context = type(
            "Context",
            (),
            {
                "allowed_entities": ["light.room", "light.studio"],
                "allowed_scenes": [],
                "allowed_scripts": [],
                "target_capabilities": {
                    target_id: capabilities.to_prompt_dict()
                    for target_id, capabilities in target_capabilities.items()
                },
            },
        )()

        plan = asyncio.run(interpreter.interpret("turn all lights off", context))

        self.assertEqual([intent.target for intent in plan.actions], ["light.room", "light.studio"])
        self.assertTrue(all(intent.action == "turn_off" for intent in plan.actions))

    def test_local_interpreter_routes_turn_all_lights_on_phrase_to_all_home_lights(self):
        interpreter = LocalInterpreter(FakeSettings())
        target_capabilities = build_target_capabilities_from_lists(
            allowed_entities=["light.room", "light.studio"],
            allowed_scenes=[],
            allowed_scripts=[],
            target_overrides={
                "light.room": {"aliases": ["room lights"], "actions": ["turn_on", "turn_off", "get_state"]},
                "light.studio": {"aliases": ["studio lights"], "actions": ["turn_on", "turn_off", "get_state"]},
            },
        )
        context = type(
            "Context",
            (),
            {
                "allowed_entities": ["light.room", "light.studio"],
                "allowed_scenes": [],
                "allowed_scripts": [],
                "target_capabilities": {
                    target_id: capabilities.to_prompt_dict()
                    for target_id, capabilities in target_capabilities.items()
                },
            },
        )()

        plan = asyncio.run(interpreter.interpret("turn all lights on", context))

        self.assertEqual([intent.target for intent in plan.actions], ["light.room", "light.studio"])
        self.assertTrue(all(intent.action == "turn_on" for intent in plan.actions))

    def test_local_interpreter_parses_explicit_brightness(self):
        interpreter = LocalInterpreter(FakeSettings())
        target_capabilities = build_target_capabilities_from_lists(
            allowed_entities=["light.office"],
            allowed_scenes=[],
            allowed_scripts=[],
        )
        context = type(
            "Context",
            (),
            {
                "allowed_entities": ["light.office"],
                "allowed_scenes": [],
                "allowed_scripts": [],
                "target_capabilities": {
                    target_id: capabilities.to_prompt_dict()
                    for target_id, capabilities in target_capabilities.items()
                },
            },
        )()

        plan = asyncio.run(interpreter.interpret("set the office lights to 50 percent", context))

        self.assertEqual(plan.primary_intent.target, "light.office")
        self.assertEqual(plan.primary_intent.parameters, {"brightness_pct": 50})

    def test_local_interpreter_strips_wake_word_and_polite_filler(self):
        interpreter = LocalInterpreter(FakeSettings())
        target_capabilities = build_target_capabilities_from_lists(
            allowed_entities=["light.room", "light.studio"],
            allowed_scenes=[],
            allowed_scripts=[],
            target_overrides={
                "light.room": {"aliases": ["room lights"], "actions": ["turn_on", "turn_off", "get_state"]},
                "light.studio": {"aliases": ["studio lights"], "actions": ["turn_on", "turn_off", "get_state"]},
            },
        )
        context = type(
            "Context",
            (),
            {
                "allowed_entities": ["light.room", "light.studio"],
                "allowed_scenes": [],
                "allowed_scripts": [],
                "states": [
                    {
                        "entity_id": "light.room",
                        "state": "off",
                        "attributes": {"entity_id": ["light.lamp_post_1", "light.lampara_a60_e27_smart_baw_9"]},
                    },
                    {
                        "entity_id": "light.studio",
                        "state": "off",
                        "attributes": {"entity_id": ["light.recording_studio_door_garden", "light.led_bulb_w509z2"]},
                    },
                ],
                "target_capabilities": {
                    target_id: capabilities.to_prompt_dict()
                    for target_id, capabilities in target_capabilities.items()
                },
            },
        )()

        plan = asyncio.run(interpreter.interpret("hey jarvis could you please shut off all of the lights for me", context))

        self.assertEqual([intent.target for intent in plan.actions], ["light.room", "light.studio"])
        self.assertTrue(all(intent.action == "turn_off" for intent in plan.actions))

    def test_local_interpreter_skips_unavailable_lights_for_generic_all_lights_request(self):
        interpreter = LocalInterpreter(FakeSettings())
        target_capabilities = build_target_capabilities_from_lists(
            allowed_entities=["light.room", "light.studio"],
            allowed_scenes=[],
            allowed_scripts=[],
            target_overrides={
                "light.room": {"aliases": ["room lights"], "actions": ["turn_on", "turn_off", "get_state"]},
                "light.studio": {"aliases": ["studio lights"], "actions": ["turn_on", "turn_off", "get_state"]},
            },
        )
        context = type(
            "Context",
            (),
            {
                "allowed_entities": ["light.room", "light.studio"],
                "allowed_scenes": [],
                "allowed_scripts": [],
                "states": [
                    {
                        "entity_id": "light.room",
                        "state": "unavailable",
                        "attributes": {},
                    },
                    {
                        "entity_id": "light.studio",
                        "state": "off",
                        "attributes": {"entity_id": ["light.recording_studio_door_garden", "light.led_bulb_w509z2"]},
                    },
                ],
                "target_capabilities": {
                    target_id: capabilities.to_prompt_dict()
                    for target_id, capabilities in target_capabilities.items()
                },
            },
        )()

        plan = asyncio.run(interpreter.interpret("turn off all lights", context))

        self.assertEqual([intent.target for intent in plan.actions], ["light.studio"])

    def test_local_interpreter_rejects_specific_room_request_when_only_studio_is_available(self):
        interpreter = LocalInterpreter(FakeSettings())
        target_capabilities = build_target_capabilities_from_lists(
            allowed_entities=["light.studio"],
            allowed_scenes=[],
            allowed_scripts=[],
            target_overrides={
                "light.studio": {"aliases": ["studio lights"], "actions": ["turn_on", "turn_off", "get_state"]},
            },
        )
        context = type(
            "Context",
            (),
            {
                "allowed_entities": ["light.studio"],
                "allowed_scenes": [],
                "allowed_scripts": [],
                "states": [
                    {
                        "entity_id": "light.studio",
                        "state": "off",
                        "attributes": {"entity_id": ["light.recording_studio_door_garden", "light.led_bulb_w509z2"]},
                    },
                ],
                "target_capabilities": {
                    target_id: capabilities.to_prompt_dict()
                    for target_id, capabilities in target_capabilities.items()
                },
            },
        )()

        with self.assertRaises(ValidationError):
            asyncio.run(interpreter.interpret("turn the room lights off", context))

    def test_local_interpreter_parses_medium_brightness(self):
        interpreter = LocalInterpreter(FakeSettings())
        target_capabilities = build_target_capabilities_from_lists(
            allowed_entities=["light.studio"],
            allowed_scenes=[],
            allowed_scripts=[],
            target_overrides={
                "light.studio": {"aliases": ["studio lights"], "actions": ["turn_on", "turn_off", "get_state"]},
            },
        )
        context = type(
            "Context",
            (),
            {
                "allowed_entities": ["light.studio"],
                "allowed_scenes": [],
                "allowed_scripts": [],
                "target_capabilities": {
                    target_id: capabilities.to_prompt_dict()
                    for target_id, capabilities in target_capabilities.items()
                },
            },
        )()

        plan = asyncio.run(interpreter.interpret("set the studio lights to medium brightness", context))

        self.assertEqual(plan.primary_intent.target, "light.studio")
        self.assertEqual(plan.primary_intent.parameters, {"brightness_pct": 50})

    def test_local_interpreter_parses_full_intensity(self):
        interpreter = LocalInterpreter(FakeSettings())
        target_capabilities = build_target_capabilities_from_lists(
            allowed_entities=["light.room"],
            allowed_scenes=[],
            allowed_scripts=[],
            target_overrides={
                "light.room": {"aliases": ["room lights"], "actions": ["turn_on", "turn_off", "get_state"]},
            },
        )
        context = type(
            "Context",
            (),
            {
                "allowed_entities": ["light.room"],
                "allowed_scenes": [],
                "allowed_scripts": [],
                "target_capabilities": {
                    target_id: capabilities.to_prompt_dict()
                    for target_id, capabilities in target_capabilities.items()
                },
            },
        )()

        plan = asyncio.run(interpreter.interpret("please turn on the room lights at full intensity", context))

        self.assertEqual(plan.primary_intent.target, "light.room")
        self.assertEqual(plan.primary_intent.parameters, {"brightness_pct": 100})

    def test_local_interpreter_parses_split_turn_off_phrase(self):
        interpreter = LocalInterpreter(FakeSettings())
        target_capabilities = build_target_capabilities_from_lists(
            allowed_entities=["light.studio"],
            allowed_scenes=[],
            allowed_scripts=[],
            target_overrides={
                "light.studio": {"aliases": ["studio lights"], "actions": ["turn_on", "turn_off", "get_state"]},
            },
        )
        context = type(
            "Context",
            (),
            {
                "allowed_entities": ["light.studio"],
                "allowed_scenes": [],
                "allowed_scripts": [],
                "target_capabilities": {
                    target_id: capabilities.to_prompt_dict()
                    for target_id, capabilities in target_capabilities.items()
                },
            },
        )()

        plan = asyncio.run(interpreter.interpret("turn the studio lights off", context))

        self.assertEqual(plan.primary_intent.target, "light.studio")
        self.assertEqual(plan.primary_intent.action, "turn_off")

    def test_local_interpreter_prefers_trailing_off_in_contradictory_phrase(self):
        interpreter = LocalInterpreter(FakeSettings())
        target_capabilities = build_target_capabilities_from_lists(
            allowed_entities=["light.room"],
            allowed_scenes=[],
            allowed_scripts=[],
            target_overrides={
                "light.room": {"aliases": ["room lights"], "actions": ["turn_on", "turn_off", "get_state"]},
            },
        )
        context = type(
            "Context",
            (),
            {
                "allowed_entities": ["light.room"],
                "allowed_scenes": [],
                "allowed_scripts": [],
                "target_capabilities": {
                    target_id: capabilities.to_prompt_dict()
                    for target_id, capabilities in target_capabilities.items()
                },
            },
        )()

        plan = asyncio.run(interpreter.interpret("turn on the room lights off", context))

        self.assertEqual(plan.primary_intent.target, "light.room")
        self.assertEqual(plan.primary_intent.action, "turn_off")

    def test_local_interpreter_collapses_duplicate_transcript_phrase(self):
        interpreter = LocalInterpreter(FakeSettings())
        target_capabilities = build_target_capabilities_from_lists(
            allowed_entities=["light.room"],
            allowed_scenes=[],
            allowed_scripts=[],
            target_overrides={
                "light.room": {"aliases": ["room lights"], "actions": ["turn_on", "turn_off", "get_state"]},
            },
        )
        context = type(
            "Context",
            (),
            {
                "allowed_entities": ["light.room"],
                "allowed_scenes": [],
                "allowed_scripts": [],
                "target_capabilities": {
                    target_id: capabilities.to_prompt_dict()
                    for target_id, capabilities in target_capabilities.items()
                },
            },
        )()

        plan = asyncio.run(
            interpreter.interpret(
                "turn the room lights off turn the room lights off",
                context,
            )
        )

        self.assertEqual(plan.primary_intent.target, "light.room")
        self.assertEqual(plan.primary_intent.action, "turn_off")

    def test_build_interpreter_uses_local_rules_when_requested(self):
        bundle = build_interpreter(FakeSettings())

        self.assertEqual(bundle.name, "local_rules")
        self.assertIsInstance(bundle.interpreter, LocalInterpreter)

    def test_local_first_interpreter_skips_primary_when_local_matches(self):
        primary = CountingInterpreter(single_action_plan("turn_on", "light.kitchen"))
        local_first = LocalFirstInterpreter(
            local=LocalInterpreter(FakeSettings()),
            primary=primary,
            primary_name="claude_cli",
        )
        context = type(
            "Context",
            (),
            {
                "allowed_entities": ["input_boolean.virtual_living_room_light"],
                "allowed_scenes": [],
                "allowed_scripts": [],
            },
        )()

        plan = asyncio.run(local_first.interpret("turn on the living room light", context))

        self.assertEqual(plan.primary_intent.target, "input_boolean.virtual_living_room_light")
        self.assertEqual(primary.calls, 0)

    def test_local_first_interpreter_uses_primary_when_local_does_not_match(self):
        primary = CountingInterpreter(single_action_plan("turn_on", "light.kitchen"))
        local_first = LocalFirstInterpreter(
            local=LocalInterpreter(FakeSettings()),
            primary=primary,
            primary_name="claude_cli",
        )
        context = type(
            "Context",
            (),
            {
                "allowed_entities": ["input_boolean.virtual_living_room_light"],
                "allowed_scenes": [],
                "allowed_scripts": [],
            },
        )()

        plan = asyncio.run(local_first.interpret("make the room comfortable", context))

        self.assertEqual(plan.primary_intent.target, "light.kitchen")
        self.assertEqual(primary.calls, 1)

    def test_local_first_interpreter_skips_primary_for_generic_lights_request(self):
        primary = CountingInterpreter(single_action_plan("turn_on", "light.kitchen"))
        local_first = LocalFirstInterpreter(
            local=LocalInterpreter(FakeSettings()),
            primary=primary,
            primary_name="claude_cli",
        )
        target_capabilities = build_target_capabilities_from_lists(
            allowed_entities=["light.room", "light.studio"],
            allowed_scenes=[],
            allowed_scripts=[],
            target_overrides={
                "light.room": {"aliases": ["room lights"], "actions": ["turn_on", "turn_off", "get_state"]},
                "light.studio": {"aliases": ["studio lights"], "actions": ["turn_on", "turn_off", "get_state"]},
            },
        )
        context = type(
            "Context",
            (),
            {
                "allowed_entities": ["light.room", "light.studio"],
                "allowed_scenes": [],
                "allowed_scripts": [],
                "target_capabilities": {
                    target_id: capabilities.to_prompt_dict()
                    for target_id, capabilities in target_capabilities.items()
                },
            },
        )()

        plan = asyncio.run(local_first.interpret("turn on the lights", context))

        self.assertEqual([intent.target for intent in plan.actions], ["light.room", "light.studio"])
        self.assertEqual(primary.calls, 0)

    def test_local_first_interpreter_skips_primary_for_polite_spoken_command(self):
        primary = CountingInterpreter(single_action_plan("turn_on", "light.kitchen"))
        local_first = LocalFirstInterpreter(
            local=LocalInterpreter(FakeSettings()),
            primary=primary,
            primary_name="claude_cli",
        )
        target_capabilities = build_target_capabilities_from_lists(
            allowed_entities=["light.room", "light.studio"],
            allowed_scenes=[],
            allowed_scripts=[],
            target_overrides={
                "light.room": {"aliases": ["room lights"], "actions": ["turn_on", "turn_off", "get_state"]},
                "light.studio": {"aliases": ["studio lights"], "actions": ["turn_on", "turn_off", "get_state"]},
            },
        )
        context = type(
            "Context",
            (),
            {
                "allowed_entities": ["light.room", "light.studio"],
                "allowed_scenes": [],
                "allowed_scripts": [],
                "states": [
                    {
                        "entity_id": "light.room",
                        "state": "off",
                        "attributes": {"entity_id": ["light.lamp_post_1", "light.lampara_a60_e27_smart_baw_9"]},
                    },
                    {
                        "entity_id": "light.studio",
                        "state": "off",
                        "attributes": {"entity_id": ["light.recording_studio_door_garden", "light.led_bulb_w509z2"]},
                    },
                ],
                "target_capabilities": {
                    target_id: capabilities.to_prompt_dict()
                    for target_id, capabilities in target_capabilities.items()
                },
            },
        )()

        plan = asyncio.run(local_first.interpret("hey jarvis could you please shut off all of the lights for me", context))

        self.assertEqual([intent.target for intent in plan.actions], ["light.room", "light.studio"])
        self.assertEqual(primary.calls, 0)

    def test_local_first_interpreter_uses_primary_for_recurring_routines(self):
        primary = CountingInterpreter(
            ActionPlan(
                actions=[Intent(action="turn_on", target="light.kitchen", parameters={})],
                routine=RoutineSpec(type="daily", time="07:00", name="Kitchen morning"),
            )
        )
        local_first = LocalFirstInterpreter(
            local=LocalInterpreter(FakeSettings()),
            primary=primary,
            primary_name="claude_cli",
        )
        context = type(
            "Context",
            (),
            {
                "allowed_entities": ["light.kitchen"],
                "allowed_scenes": [],
                "allowed_scripts": [],
            },
        )()

        plan = asyncio.run(
            local_first.interpret("create a routine to turn on the kitchen lights every day at 7", context)
        )

        self.assertIsNotNone(plan.routine)
        self.assertEqual(primary.calls, 1)

    def test_local_first_interpreter_uses_primary_for_saved_scenes(self):
        primary = CountingInterpreter(
            ActionPlan(
                actions=[Intent(action="turn_on", target="light.kitchen", parameters={})],
                saved_scene=SavedSceneSpec(name="Kitchen mode"),
            )
        )
        local_first = LocalFirstInterpreter(
            local=LocalInterpreter(FakeSettings()),
            primary=primary,
            primary_name="claude_cli",
        )
        context = type(
            "Context",
            (),
            {
                "allowed_entities": ["light.kitchen"],
                "allowed_scenes": [],
                "allowed_scripts": [],
            },
        )()

        plan = asyncio.run(
            local_first.interpret("create a scene called kitchen mode that turns on the kitchen lights", context)
        )

        self.assertIsNotNone(plan.saved_scene)
        self.assertEqual(primary.calls, 1)

    def test_claude_prefix_skips_local_first_fast_path(self):
        primary = CountingInterpreter(single_action_plan("turn_on", "light.kitchen"))
        local_first = LocalFirstInterpreter(
            local=LocalInterpreter(FakeSettings()),
            primary=primary,
            primary_name="claude_cli",
        )
        context = type(
            "Context",
            (),
            {
                "allowed_entities": ["input_boolean.virtual_living_room_light"],
                "allowed_scenes": [],
                "allowed_scripts": [],
            },
        )()

        plan = asyncio.run(local_first.interpret("claude turn on the living room light", context))

        self.assertEqual(plan.primary_intent.target, "light.kitchen")
        self.assertEqual(primary.calls, 1)
        self.assertEqual(primary.last_text, "turn on the living room light")

    def test_claude_prefix_on_fallback_interpreter_does_not_use_local_fallback(self):
        fallback = FallbackInterpreter(
            primary=FailingInterpreter(),
            fallback=LocalInterpreter(FakeSettings()),
            primary_name="claude_cli",
        )
        context = type(
            "Context",
            (),
            {
                "allowed_entities": ["input_boolean.virtual_living_room_light"],
                "allowed_scenes": [],
                "allowed_scripts": [],
            },
        )()

        with self.assertRaises(UpstreamServiceError):
            asyncio.run(fallback.interpret("claude turn on the living room light", context))


class ClaudeCliPromptCompactionTests(unittest.TestCase):
    def test_select_prompt_targets_prioritizes_matching_aliases(self):
        prompt_targets = {
            "light.office": {
                "domain": "light",
                "aliases": ["office", "office lights"],
                "actions": {"turn_off": {"parameters": {}}},
            },
            "light.kitchen": {
                "domain": "light",
                "aliases": ["kitchen", "kitchen light"],
                "actions": {"turn_off": {"parameters": {}}},
            },
            "scene.movie_time": {
                "domain": "scene",
                "aliases": ["movie time"],
                "actions": {"activate_scene": {"parameters": {}}},
            },
        }

        selected = _select_prompt_targets(
            text="turn off the office lights",
            target_capabilities=prompt_targets,
            max_targets=1,
        )

        self.assertEqual(list(selected), ["light.office"])

    def test_select_prompt_targets_excludes_zero_match_scene_when_room_target_matches(self):
        prompt_targets = {
            "light.office": {
                "domain": "light",
                "aliases": ["office", "office lights"],
                "actions": {"turn_on": {"parameters": {"brightness_pct": {"kind": "integer"}}}},
            },
            "scene.guest_mode": {
                "domain": "scene",
                "aliases": ["guest mode"],
                "actions": {"activate_scene": {"parameters": {}}},
            },
            "script.prepare_bedtime": {
                "domain": "script",
                "aliases": ["bedtime"],
                "actions": {"run_script": {"parameters": {}}},
            },
        }

        selected = _select_prompt_targets(
            text="i have visitors in my office, adjust the lights to make it more comfortable",
            target_capabilities=prompt_targets,
            max_targets=3,
        )

        self.assertEqual(list(selected), ["light.office"])

    def test_select_visible_states_prefers_selected_targets(self):
        states = [
            {
                "entity_id": "light.office",
                "state": "on",
                "attributes": {"friendly_name": "Office"},
            },
            {
                "entity_id": "light.kitchen",
                "state": "off",
                "attributes": {"friendly_name": "Kitchen"},
            },
        ]

        selected_states = _select_visible_states(
            states=states,
            selected_target_ids={"light.office"},
            max_states=1,
        )

        self.assertEqual(
            selected_states,
            [
                {
                    "entity_id": "light.office",
                    "state": "on",
                    "friendly_name": "Office",
                }
            ],
        )
