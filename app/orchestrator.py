from __future__ import annotations

from datetime import datetime
import logging
from typing import Any
from zoneinfo import ZoneInfo

from app.capabilities import build_target_capabilities, build_target_capabilities_from_lists
from app.command_routing import extract_forced_claude_request
from app.errors import ValidationError
from app.models import ActionPlan, ClaudeContext, CommandResponse, Intent
from app.voice_safety import sanitize_voice_input
from app.weather_briefing import WeatherBriefingService


logger = logging.getLogger(__name__)


class CommandOrchestrator:
    def __init__(
        self,
        settings: Any,
        home_assistant: Any,
        interpreter: Any,
        scheduler: Any | None = None,
        routines: Any | None = None,
        saved_scenes: Any | None = None,
        state_memory: Any | None = None,
        audio_output: Any | None = None,
    ):
        self._settings = settings
        self._home_assistant = home_assistant
        self._interpreter = interpreter
        self._scheduler = scheduler
        self._routines = routines
        self._saved_scenes = saved_scenes
        self._state_memory = state_memory
        self._audio_output = audio_output
        self._timezone = ZoneInfo(settings.local_timezone)
        self._base_target_capabilities = build_target_capabilities(settings)
        self._weather_briefing = WeatherBriefingService(settings, home_assistant)

    async def process(self, text: str, dry_run: bool) -> CommandResponse:
        try:
            text = sanitize_voice_input(text)
        except ValueError as exc:
            if str(exc) == "repetition_loop":
                raise ValidationError(
                    "The spoken command looked corrupted or repeated. Please try again."
                ) from exc
            raise

        logger.info("Input text: %s", text)
        forced_claude_request = extract_forced_claude_request(text)
        if forced_claude_request is not None:
            logger.info("Claude prefix detected; local orchestration shortcuts are disabled for this request.")

        if forced_claude_request is None and self._weather_briefing.should_handle(text):
            briefing = await self._weather_briefing.build_briefing()
            assistant_response = str(briefing["assistant_response"])
            logger.info("Matched local weather briefing.")
            if not dry_run and self._audio_output is not None:
                await self._audio_output.enqueue(
                    str(briefing.get("spoken_response") or assistant_response)
                )
            return CommandResponse(
                text=text,
                actions=[],
                assistant_response=assistant_response,
                executed=False,
                scheduled=False,
                dry_run=dry_run,
                results=[
                    {
                        "service": "weather.briefing",
                        "target": {"entity_id": briefing["weather_entity_id"]},
                        "response": briefing,
                    }
                ],
                rationale="Matched local weather briefing.",
            )

        states = await self._home_assistant.get_states()
        target_capabilities = self._build_effective_target_capabilities(states)
        visible_states = [
            state
            for state in states
            if state.get("entity_id") in target_capabilities
        ]
        prompt_target_capabilities = self._build_prompt_target_capabilities(
            visible_states,
            target_capabilities,
        )
        previous_states = {}
        if self._state_memory is not None:
            previous_states = await self._state_memory.get_previous_states(target_capabilities.keys())
        context = ClaudeContext(
            time_context=self._build_time_context(),
            states=visible_states,
            previous_states=previous_states,
            allowed_entities=[
                target_id
                for target_id, capabilities in target_capabilities.items()
                if capabilities.kind == "entity"
            ],
            allowed_scenes=[
                target_id
                for target_id, capabilities in target_capabilities.items()
                if capabilities.kind == "scene"
            ],
            allowed_scripts=[
                target_id
                for target_id, capabilities in target_capabilities.items()
                if capabilities.kind == "script"
            ],
            target_capabilities=prompt_target_capabilities,
        )
        if forced_claude_request is None and self._saved_scenes is not None:
            saved_scene = await self._saved_scenes.match_scene_request(text)
            if saved_scene is not None:
                return await self.activate_saved_scene(saved_scene, text=text, dry_run=dry_run)

        plan = await self._interpreter.interpret(text, context)
        self._validate_plan(plan, target_capabilities)

        logger.info("Interpreted action plan: %s", plan.model_dump_json())

        scheduled = False
        scheduled_job_id: str | None = None
        routine_created = False
        routine_id: str | None = None
        saved_scene_created = False
        saved_scene_id: str | None = None
        if dry_run:
            if plan.saved_scene is not None:
                results = [
                    {
                        "message": "Dry run only. No saved scene created.",
                        "action": intent.action,
                        "target": {"entity_id": intent.target},
                        "parameters": intent.parameters,
                        "saved_scene": plan.saved_scene.model_dump(mode="json"),
                    }
                    for intent in plan.actions
                ]
            elif plan.routine is not None:
                results = [
                    {
                        "message": "Dry run only. No routine created.",
                        "action": intent.action,
                        "target": {"entity_id": intent.target},
                        "parameters": intent.parameters,
                        "routine": plan.routine.model_dump(mode="json"),
                    }
                    for intent in plan.actions
                ]
            elif plan.schedule is not None:
                results = [
                    {
                        "message": "Dry run only. No Home Assistant service scheduled.",
                        "action": intent.action,
                        "target": {"entity_id": intent.target},
                        "parameters": intent.parameters,
                        "schedule": plan.schedule.model_dump(mode="json"),
                    }
                    for intent in plan.actions
                ]
            else:
                results = [
                    {
                        "message": "Dry run only. No Home Assistant service executed.",
                        "action": intent.action,
                        "target": {"entity_id": intent.target},
                        "parameters": intent.parameters,
                    }
                    for intent in plan.actions
                ]
            executed = False
        elif plan.saved_scene is not None:
            if self._saved_scenes is None:
                raise ValidationError("Saved scenes are not available.")
            saved_scene_id = await self._saved_scenes.create_scene(text, plan)
            saved_scene_created = True
            results = [
                {
                    "message": "Saved scene created.",
                    "action": intent.action,
                    "target": {"entity_id": intent.target},
                    "parameters": intent.parameters,
                    "saved_scene": plan.saved_scene.model_dump(mode="json"),
                }
                for intent in plan.actions
            ]
            executed = False
        elif plan.routine is not None:
            if self._routines is None:
                raise ValidationError("Routines are not available.")
            routine_id = await self._routines.create_routine(text, plan)
            routine_created = True
            results = [
                {
                    "message": "Routine created for recurring execution.",
                    "action": intent.action,
                    "target": {"entity_id": intent.target},
                    "parameters": intent.parameters,
                    "routine": plan.routine.model_dump(mode="json"),
                }
                for intent in plan.actions
            ]
            executed = False
        elif plan.schedule is not None:
            if self._scheduler is None:
                raise ValidationError("Scheduling is not available.")
            scheduled_job_id = await self._scheduler.schedule_plan(text, plan)
            scheduled = True
            results = [
                {
                    "message": "Scheduled for later execution.",
                    "action": intent.action,
                    "target": {"entity_id": intent.target},
                    "parameters": intent.parameters,
                }
                for intent in plan.actions
            ]
            executed = False
        else:
            if self._state_memory is not None:
                await self._state_memory.capture_before_plan(plan)
            results = await self._home_assistant.execute_plan(plan)
            executed = True

        logger.info("Executed: %s", executed)
        generated_assistant_response = self._build_assistant_response(
            plan=plan,
            results=results,
            executed=executed,
            dry_run=dry_run,
            scheduled=scheduled,
            routine_created=routine_created,
            saved_scene_created=saved_scene_created,
        )
        assistant_response = (
            generated_assistant_response
            if plan.saved_scene is not None
            else plan.assistant_response or generated_assistant_response
        )
        if not dry_run and self._audio_output is not None:
            await self._audio_output.enqueue(
                self._build_spoken_response(
                    plan=plan,
                    assistant_response=assistant_response,
                    executed=executed,
                    scheduled=scheduled,
                )
            )

        return CommandResponse(
            text=text,
            actions=plan.actions,
            assistant_response=assistant_response,
            executed=executed,
            scheduled=scheduled,
            routine_created=routine_created,
            saved_scene_created=saved_scene_created,
            dry_run=dry_run,
            results=results,
            rationale=plan.rationale,
            schedule=plan.schedule,
            scheduled_job_id=scheduled_job_id,
            routine_id=routine_id,
            saved_scene_id=saved_scene_id,
        )

    async def activate_saved_scene(
        self,
        saved_scene: Any,
        *,
        text: str,
        dry_run: bool,
    ) -> CommandResponse:
        states = await self._home_assistant.get_states()
        target_capabilities = self._build_effective_target_capabilities(states)
        plan = ActionPlan(
            actions=list(saved_scene.actions),
            rationale=f"Matched saved scene: {saved_scene.name}.",
            assistant_response=f"Okay. I activated {saved_scene.name}.",
        )
        self._validate_plan(plan, target_capabilities)

        if dry_run:
            results = [
                {
                    "message": "Dry run only. No saved scene action executed.",
                    "action": intent.action,
                    "target": {"entity_id": intent.target},
                    "parameters": intent.parameters,
                    "saved_scene_id": saved_scene.scene_id,
                    "saved_scene_name": saved_scene.name,
                }
                for intent in plan.actions
            ]
            executed = False
        else:
            if self._state_memory is not None:
                await self._state_memory.capture_before_plan(plan)
            results = await self._home_assistant.execute_plan(plan)
            executed = True

        assistant_response = plan.assistant_response or self._build_assistant_response(
            plan=plan,
            results=results,
            executed=executed,
            dry_run=dry_run,
            scheduled=False,
            routine_created=False,
            saved_scene_created=False,
        )
        if not dry_run and self._audio_output is not None:
            await self._audio_output.enqueue(
                self._build_spoken_response(
                    plan=plan,
                    assistant_response=assistant_response,
                    executed=executed,
                    scheduled=False,
                )
            )

        return CommandResponse(
            text=text,
            actions=plan.actions,
            assistant_response=assistant_response,
            executed=executed,
            scheduled=False,
            routine_created=False,
            saved_scene_created=False,
            dry_run=dry_run,
            results=results,
            rationale=plan.rationale,
            saved_scene_id=getattr(saved_scene, "scene_id", None),
        )

    def _build_prompt_target_capabilities(
        self,
        visible_states: list[dict[str, Any]],
        target_capabilities: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        states_by_entity_id = {
            state.get("entity_id"): state
            for state in visible_states
            if state.get("entity_id")
        }

        prompt_target_capabilities: dict[str, dict[str, Any]] = {}
        for target_id, capabilities in target_capabilities.items():
            prompt_dict = capabilities.to_prompt_dict()
            aliases = set(prompt_dict.get("aliases", []))

            state = states_by_entity_id.get(target_id)
            if state:
                friendly_name = state.get("attributes", {}).get("friendly_name")
                if isinstance(friendly_name, str) and friendly_name.strip():
                    aliases.add(friendly_name.strip())
                    aliases.add(friendly_name.strip().lower())

            prompt_dict["aliases"] = sorted(alias for alias in aliases if alias)
            prompt_target_capabilities[target_id] = prompt_dict

        return prompt_target_capabilities

    def _build_effective_target_capabilities(
        self,
        states: list[dict[str, Any]],
    ) -> dict[str, Any]:
        target_capabilities = dict(self._base_target_capabilities)

        if not getattr(self._settings, "auto_discover_entities", False):
            return self._filter_unavailable_target_capabilities(states, target_capabilities)

        auto_allowed_entities = [
            entity_id
            for entity_id in (
                self._discover_entity_id_from_state(state)
                for state in states
            )
            if entity_id and entity_id not in target_capabilities
        ]

        if not auto_allowed_entities:
            return self._filter_unavailable_target_capabilities(states, target_capabilities)

        auto_target_capabilities = build_target_capabilities_from_lists(
            allowed_entities=auto_allowed_entities,
            allowed_scenes=[],
            allowed_scripts=[],
            target_overrides=self._settings.target_overrides,
        )
        target_capabilities.update(auto_target_capabilities)
        return self._filter_unavailable_target_capabilities(states, target_capabilities)

    def _filter_unavailable_target_capabilities(
        self,
        states: list[dict[str, Any]],
        target_capabilities: dict[str, Any],
    ) -> dict[str, Any]:
        states_by_entity_id = {
            state.get("entity_id"): state
            for state in states
            if isinstance(state.get("entity_id"), str)
        }

        filtered_capabilities: dict[str, Any] = {}
        for target_id, capabilities in target_capabilities.items():
            if getattr(capabilities, "kind", "entity") != "entity":
                filtered_capabilities[target_id] = capabilities
                continue

            state = states_by_entity_id.get(target_id)
            if not isinstance(state, dict):
                filtered_capabilities[target_id] = capabilities
                continue

            entity_state = str(state.get("state", "")).strip().lower()
            if entity_state in {"unavailable", "unknown"}:
                continue

            filtered_capabilities[target_id] = capabilities

        return filtered_capabilities

    def _discover_entity_id_from_state(self, state: dict[str, Any]) -> str | None:
        entity_id = state.get("entity_id")
        if not isinstance(entity_id, str) or "." not in entity_id:
            return None

        if entity_id in getattr(self._settings, "ignored_entities", []):
            return None

        domain = entity_id.split(".", 1)[0].lower()
        if domain not in getattr(self._settings, "auto_discover_domains", []):
            return None

        entity_state = str(state.get("state", "")).lower()
        if (
            not getattr(self._settings, "auto_discover_include_unavailable", False)
            and entity_state in {"unavailable", "unknown"}
        ):
            return None

        return entity_id

    def _validate_plan(
        self,
        plan: ActionPlan,
        target_capabilities: dict[str, Any],
    ) -> None:
        self._validate_schedule(plan)
        self._validate_routine(plan, target_capabilities)
        self._validate_saved_scene(plan, target_capabilities)
        for intent in plan.actions:
            self._validate_intent(intent, target_capabilities)

    def _validate_schedule(self, plan: ActionPlan) -> None:
        if plan.schedule is None:
            return

        if not getattr(self._settings, "scheduling_enabled", False):
            raise ValidationError("Scheduling is disabled.")

        if plan.schedule.type == "at" and plan.schedule.execute_at is not None:
            execute_at = plan.schedule.execute_at
            if execute_at.tzinfo is None:
                execute_at = execute_at.replace(tzinfo=self._timezone)
            if execute_at.astimezone(self._timezone) <= datetime.now(self._timezone):
                raise ValidationError("Scheduled execution time must be in the future.")

    def _validate_routine(self, plan: ActionPlan, target_capabilities: dict[str, Any]) -> None:
        if plan.routine is None:
            return

        if plan.schedule is not None:
            raise ValidationError("Use either a one-time schedule or a recurring routine, not both.")
        if not getattr(self._settings, "routines_enabled", False):
            raise ValidationError("Routines are disabled.")
        if not plan.actions:
            raise ValidationError("Routines require at least one action.")

        for intent in plan.actions:
            target_definition = target_capabilities.get(intent.target)
            if target_definition is None:
                continue
            if target_definition.security == "high" or intent.action in {"unlock"}:
                raise ValidationError("Routines cannot include high-security or unlock actions.")

    def _validate_saved_scene(self, plan: ActionPlan, target_capabilities: dict[str, Any]) -> None:
        if plan.saved_scene is None:
            return

        if plan.schedule is not None or plan.routine is not None:
            raise ValidationError("Use a saved scene, schedule, or routine, not more than one at once.")
        if not getattr(self._settings, "saved_scenes_enabled", False):
            raise ValidationError("Saved scenes are disabled.")
        if not plan.actions:
            raise ValidationError("Saved scenes require at least one action.")

        for intent in plan.actions:
            target_definition = target_capabilities.get(intent.target)
            if target_definition is None:
                continue
            if target_definition.security == "high" or intent.action in {"unlock"}:
                raise ValidationError("Saved scenes cannot include high-security or unlock actions.")

    def _validate_intent(
        self,
        intent: Intent,
        target_capabilities: dict[str, Any],
    ) -> None:
        if intent.target == "UNSAFE":
            raise ValidationError("Claude marked the request as unsafe or ambiguous.")

        target_definition = target_capabilities.get(intent.target)
        if target_definition is None:
            raise ValidationError(f"Target not allowed: {intent.target}")

        action_definition = target_definition.action_definition(intent.action)
        if action_definition is None:
            raise ValidationError(
                f"Action '{intent.action}' is not allowed for target {intent.target}."
            )

        extra_parameters = set(intent.parameters) - set(action_definition.parameter_specs)
        if extra_parameters:
            raise ValidationError(
                f"Unsupported parameters requested for {intent.target}: {sorted(extra_parameters)}"
            )

        for parameter_name, parameter_value in intent.parameters.items():
            action_definition.parameter_specs[parameter_name].validate(parameter_name, parameter_value)

    def _build_time_context(self) -> dict[str, Any]:
        now = datetime.now(self._timezone)
        return {
            "current_time": now.isoformat(),
            "timezone": self._settings.local_timezone,
        }

    def _build_assistant_response(
        self,
        *,
        plan: ActionPlan,
        results: list[dict[str, Any]],
        executed: bool,
        dry_run: bool,
        scheduled: bool,
        routine_created: bool,
        saved_scene_created: bool,
    ) -> str:
        if plan.saved_scene is not None:
            summary = self._summarize_actions(plan.actions)
            if dry_run:
                return f"I would save {plan.saved_scene.name} as a scene for {summary}."
            if saved_scene_created:
                return f"Okay. I saved {plan.saved_scene.name} as a scene."

        if plan.routine is not None:
            routine_text = self._describe_routine(plan)
            summary = self._summarize_actions(plan.actions)
            if dry_run:
                return f"I would create a routine to {summary} {routine_text}."
            if routine_created:
                return f"Okay. I created a routine to {summary} {routine_text}."

        if scheduled and plan.schedule is not None:
            schedule_text = self._describe_schedule(plan.schedule)
            summary = self._summarize_actions(plan.actions)
            return f"Okay. I scheduled {summary} {schedule_text}."

        if dry_run:
            return self._build_dry_run_response(plan.actions)

        if not executed:
            return "I processed the request, but nothing was executed."

        if len(plan.actions) == 1:
            intent = plan.actions[0]
            return self._describe_single_action(intent)

        summary = self._summarize_actions(plan.actions)
        return f"Done. I applied {len(plan.actions)} actions to {summary}."

    def _build_spoken_response(
        self,
        *,
        plan: ActionPlan,
        assistant_response: str,
        executed: bool,
        scheduled: bool,
    ) -> str:
        if (
            getattr(self._settings, "audio_response_fast_ack_for_local", True)
            and executed
            and not scheduled
            and self._is_local_fast_action_plan(plan)
        ):
            fast_spoken_response = self._build_fast_local_spoken_response(plan)
            fallback_ack = str(getattr(self._settings, "audio_response_fast_ack_text", "Done.")).strip()
            ack_mode = str(
                getattr(self._settings, "audio_response_local_ack_mode", "descriptive")
            ).strip().lower()

            if ack_mode == "generic" and fallback_ack:
                return fallback_ack

            if fast_spoken_response:
                return fast_spoken_response

            if fallback_ack:
                return fallback_ack

        return assistant_response

    def _describe_routine(self, plan: ActionPlan) -> str:
        if plan.routine is None:
            return "on a recurring schedule"
        if plan.routine.type == "daily":
            return f"every day at {plan.routine.time}"
        return "on a recurring schedule"

    def _is_local_fast_action_plan(self, plan: ActionPlan) -> bool:
        for intent in plan.actions:
            if intent.action == "get_state":
                return False
            if not (intent.rationale or "").startswith("Matched local"):
                return False
        return True

    def _build_dry_run_response(self, actions: list[Intent]) -> str:
        if len(actions) == 1:
            description = self._describe_single_action(actions[0])
            replacements = (
                ("Done. I turned on ", "I would turn on "),
                ("Done. I turned off ", "I would turn off "),
                ("Done. I activated ", "I would activate "),
                ("Done. I ran ", "I would run "),
                ("Done. I locked ", "I would lock "),
                ("Done. I unlocked ", "I would unlock "),
                ("Done. I opened ", "I would open "),
                ("Done. I closed ", "I would close "),
                ("Done. I stopped ", "I would stop "),
                ("Done. I started ", "I would start "),
                ("Done. I paused ", "I would pause "),
                ("Done. I sent ", "I would send "),
                ("Done. I set ", "I would set "),
                ("Done. I updated ", "I would update "),
                ("I checked the state of ", "I would check the state of "),
            )
            for prefix, replacement in replacements:
                if description.startswith(prefix):
                    return f"{replacement}{description.removeprefix(prefix)}"

        summary = self._summarize_actions(actions)
        return f"I would apply {len(actions)} actions to {summary}."

    def _build_fast_local_spoken_response(self, plan: ActionPlan) -> str | None:
        if len(plan.actions) != 1:
            return None

        intent = plan.actions[0]
        target_name = self._friendly_spoken_target_name(intent.target)

        if intent.action == "turn_on":
            detail = self._describe_turn_on_parameters(intent.parameters)
            if detail:
                return f"{target_name} {detail}."
            return f"{target_name} on."

        if intent.action == "turn_off":
            return f"{target_name} off."

        if intent.action == "activate_scene":
            return f"{target_name} activated."

        if intent.action == "run_script":
            return f"{target_name} run."

        return None

    def _describe_schedule(self, schedule: Any) -> str:
        if schedule.type == "delay" and schedule.delay_seconds is not None:
            delay_seconds = int(schedule.delay_seconds)
            if delay_seconds < 60:
                value = delay_seconds
                unit = "second" if value == 1 else "seconds"
            elif delay_seconds % 3600 == 0:
                value = delay_seconds // 3600
                unit = "hour" if value == 1 else "hours"
            elif delay_seconds % 60 == 0:
                value = delay_seconds // 60
                unit = "minute" if value == 1 else "minutes"
            else:
                value = delay_seconds
                unit = "seconds"
            return f"in {value} {unit}"

        if schedule.type == "at" and schedule.execute_at is not None:
            execute_at = schedule.execute_at.astimezone(self._timezone)
            return f"at {execute_at.strftime('%H:%M')}"

        return "for later"

    def _describe_single_action(self, intent: Intent) -> str:
        target_name = self._friendly_target_name(intent.target)

        if intent.action == "turn_on":
            detail = self._describe_turn_on_parameters(intent.parameters)
            if detail:
                return f"Done. I set {target_name} {detail}."
            return f"Done. I turned on {target_name}."

        if intent.action == "turn_off":
            return f"Done. I turned off {target_name}."
        if intent.action == "activate_scene":
            return f"Done. I activated {target_name}."
        if intent.action == "run_script":
            return f"Done. I ran {target_name}."
        if intent.action == "get_state":
            return f"I checked the state of {target_name}."
        if intent.action == "lock":
            return f"Done. I locked {target_name}."
        if intent.action == "unlock":
            return f"Done. I unlocked {target_name}."
        if intent.action == "open_cover":
            return f"Done. I opened {target_name}."
        if intent.action == "close_cover":
            return f"Done. I closed {target_name}."
        if intent.action == "stop_cover":
            return f"Done. I stopped {target_name}."
        if intent.action == "set_cover_position":
            position = intent.parameters.get("position")
            return f"Done. I set {target_name} to {position}% open."
        if intent.action == "set_temperature":
            temperature = intent.parameters.get("temperature")
            return f"Done. I set {target_name} to {temperature} degrees."
        if intent.action == "set_hvac_mode":
            mode = intent.parameters.get("hvac_mode")
            return f"Done. I set {target_name} to {mode} mode."
        if intent.action == "set_fan_percentage":
            percentage = intent.parameters.get("percentage")
            return f"Done. I set {target_name} to {percentage}% speed."
        if intent.action == "set_media_volume":
            volume_level = intent.parameters.get("volume_level")
            try:
                percentage = int(round(float(volume_level) * 100))
            except (TypeError, ValueError):
                percentage = volume_level
            return f"Done. I set {target_name} volume to {percentage}%."
        if intent.action == "media_play":
            return f"Done. I started playback on {target_name}."
        if intent.action == "media_pause":
            return f"Done. I paused playback on {target_name}."
        if intent.action == "media_stop":
            return f"Done. I stopped playback on {target_name}."
        if intent.action == "media_next_track":
            return f"Done. I skipped to the next track on {target_name}."
        if intent.action == "media_previous_track":
            return f"Done. I went to the previous track on {target_name}."
        if intent.action == "vacuum_start":
            return f"Done. I started {target_name}."
        if intent.action == "vacuum_pause":
            return f"Done. I paused {target_name}."
        if intent.action == "vacuum_return_to_base":
            return f"Done. I sent {target_name} back to its base."
        if intent.action == "select_option":
            option = intent.parameters.get("option")
            return f"Done. I set {target_name} to {option}."
        if intent.action == "set_value":
            value = intent.parameters.get("value")
            return f"Done. I set {target_name} to {value}."

        return f"Done. I updated {target_name}."

    def _summarize_actions(self, actions: list[Intent]) -> str:
        if not actions:
            return "nothing"

        unique_targets = []
        seen = set()
        for intent in actions:
            if intent.target in seen:
                continue
            seen.add(intent.target)
            unique_targets.append(self._friendly_target_name(intent.target))

        if len(unique_targets) == 1:
            return unique_targets[0]
        if len(unique_targets) == 2:
            return f"{unique_targets[0]} and {unique_targets[1]}"
        return f"{', '.join(unique_targets[:-1])}, and {unique_targets[-1]}"

    def _friendly_target_name(self, target: str) -> str:
        return target.split(".", 1)[1].replace("_", " ")

    def _friendly_spoken_target_name(self, target: str) -> str:
        target_name = self._friendly_target_name(target)
        if target.startswith("light.") and "light" not in target_name:
            target_name = f"{target_name} lights"
        return target_name[:1].upper() + target_name[1:]

    def _describe_turn_on_parameters(self, parameters: dict[str, Any]) -> str | None:
        details: list[str] = []

        rgb_color = parameters.get("rgb_color")
        if isinstance(rgb_color, (list, tuple)) and len(rgb_color) == 3:
            color_name = self._rgb_to_basic_color_name(rgb_color)
            if color_name:
                details.append(f"to {color_name}")

        color_temp_kelvin = parameters.get("color_temp_kelvin")
        if isinstance(color_temp_kelvin, int):
            warmth = "warm white" if color_temp_kelvin <= 3500 else "cool white"
            details.append(f"to {warmth}")

        brightness_pct = parameters.get("brightness_pct")
        if isinstance(brightness_pct, int):
            details.append(f"at {brightness_pct}% brightness")
        elif isinstance(parameters.get("brightness"), int):
            brightness = parameters["brightness"]
            pct = int(round((brightness / 255) * 100))
            details.append(f"at {pct}% brightness")

        transition = parameters.get("transition")
        if isinstance(transition, (int, float)) and transition > 0:
            if float(transition).is_integer():
                transition_text = str(int(transition))
            else:
                transition_text = str(transition)
            details.append(f"with a {transition_text}s transition")

        if not details:
            return None

        return " ".join(details)

    def _rgb_to_basic_color_name(self, rgb_color: Any) -> str | None:
        try:
            red, green, blue = [int(channel) for channel in rgb_color]
        except (TypeError, ValueError):
            return None

        if red >= 220 and green <= 80 and blue <= 80:
            return "red"
        if green >= 220 and red <= 80 and blue <= 80:
            return "green"
        if blue >= 220 and red <= 80 and green <= 80:
            return "blue"
        if red >= 220 and green >= 120 and blue <= 80:
            return "orange"
        if red >= 220 and green >= 220 and blue <= 120:
            return "yellow"
        if red >= 220 and blue >= 220 and green <= 120:
            return "magenta"
        if green >= 180 and blue >= 180 and red <= 120:
            return "cyan"
        if red >= 230 and green >= 230 and blue >= 230:
            return "white"
        return None
