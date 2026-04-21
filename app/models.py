from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


ALLOWED_ACTIONS = (
    "turn_on",
    "turn_off",
    "activate_scene",
    "run_script",
    "get_state",
    "lock",
    "unlock",
    "open_cover",
    "close_cover",
    "stop_cover",
    "set_cover_position",
    "set_temperature",
    "set_hvac_mode",
    "set_fan_percentage",
    "set_media_volume",
    "media_play",
    "media_pause",
    "media_stop",
    "media_next_track",
    "media_previous_track",
    "vacuum_start",
    "vacuum_pause",
    "vacuum_return_to_base",
    "select_option",
    "set_value",
)

MAX_ACTIONS_PER_PLAN = 32

AllowedAction = Literal[
    "turn_on",
    "turn_off",
    "activate_scene",
    "run_script",
    "get_state",
    "lock",
    "unlock",
    "open_cover",
    "close_cover",
    "stop_cover",
    "set_cover_position",
    "set_temperature",
    "set_hvac_mode",
    "set_fan_percentage",
    "set_media_volume",
    "media_play",
    "media_pause",
    "media_stop",
    "media_next_track",
    "media_previous_track",
    "vacuum_start",
    "vacuum_pause",
    "vacuum_return_to_base",
    "select_option",
    "set_value",
]


class CommandRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    dry_run: bool = False
    source: str | None = Field(default=None, max_length=80)


class AssistCommandRequest(CommandRequest):
    source: str | None = Field(default="assist_conversation", max_length=80)


class Intent(BaseModel):
    action: AllowedAction
    target: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    rationale: str | None = None


class ScheduleSpec(BaseModel):
    type: Literal["delay", "at"]
    delay_seconds: int | None = Field(default=None, ge=1, le=31_536_000)
    execute_at: datetime | None = None

    @model_validator(mode="after")
    def _validate_shape(self) -> "ScheduleSpec":
        if self.type == "delay":
            if self.delay_seconds is None or self.execute_at is not None:
                raise ValueError("Delay schedules require delay_seconds only.")
            return self

        if self.execute_at is None:
            raise ValueError("Absolute schedules require execute_at.")
        if self.delay_seconds is not None:
            raise ValueError("Absolute schedules cannot include delay_seconds.")
        return self


class RoutineSpec(BaseModel):
    type: Literal["daily"] = "daily"
    time: str = Field(pattern=r"^\d{2}:\d{2}$")
    name: str | None = Field(default=None, max_length=80)
    timezone: str | None = None

    @model_validator(mode="after")
    def _validate_time(self) -> "RoutineSpec":
        hour_text, minute_text = self.time.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
        if hour > 23 or minute > 59:
            raise ValueError("Routine time must be a valid HH:MM clock time.")
        return self


class SavedSceneSpec(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    aliases: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def _normalize(self) -> "SavedSceneSpec":
        self.name = " ".join(self.name.split())
        if not self.name:
            raise ValueError("Saved scene name cannot be blank.")

        normalized_aliases: list[str] = []
        seen = set()
        for alias in self.aliases:
            normalized_alias = " ".join(str(alias).split())
            if not normalized_alias:
                continue
            lowered = normalized_alias.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            normalized_aliases.append(normalized_alias)
        self.aliases = normalized_aliases
        return self


class ActionPlan(BaseModel):
    actions: list[Intent] = Field(min_length=0, max_length=MAX_ACTIONS_PER_PLAN)
    rationale: str | None = None
    schedule: ScheduleSpec | None = None
    routine: RoutineSpec | None = None
    saved_scene: SavedSceneSpec | None = None
    assistant_response: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_single_intent(cls, value: Any) -> Any:
        if isinstance(value, Intent):
            return {"actions": [value]}

        if isinstance(value, dict):
            if "actions" in value:
                return value

            if "action" in value and "target" in value:
                return {"actions": [value]}

        return value

    @property
    def primary_intent(self) -> Intent:
        return self.actions[0]


class CommandResponse(BaseModel):
    text: str
    actions: list[Intent]
    assistant_response: str | None = None
    executed: bool
    scheduled: bool = False
    routine_created: bool = False
    saved_scene_created: bool = False
    dry_run: bool
    results: list[dict[str, Any]]
    rationale: str | None = None
    schedule: ScheduleSpec | None = None
    scheduled_job_id: str | None = None
    routine_id: str | None = None
    saved_scene_id: str | None = None
    source: str | None = None
    intent: Intent | None = None
    result: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _populate_legacy_fields(self) -> "CommandResponse":
        if self.intent is None and self.actions:
            self.intent = self.actions[0]

        if self.result is None:
            if self.scheduled and self.schedule is not None:
                self.result = {
                    "message": f"Scheduled {len(self.actions)} action(s).",
                    "steps": len(self.actions),
                    "scheduled_job_id": self.scheduled_job_id,
                }
            elif self.routine_created:
                self.result = {
                    "message": f"Created routine with {len(self.actions)} action(s).",
                    "steps": len(self.actions),
                    "routine_id": self.routine_id,
                }
            elif self.saved_scene_created:
                self.result = {
                    "message": f"Created saved scene with {len(self.actions)} action(s).",
                    "steps": len(self.actions),
                    "saved_scene_id": self.saved_scene_id,
                }
            elif self.results:
                if len(self.results) == 1:
                    self.result = self.results[0]
                else:
                    self.result = {
                        "message": f"Executed {len(self.results)} actions.",
                        "steps": len(self.results),
                    }

        return self


class ClaudeContext(BaseModel):
    time_context: dict[str, Any] = Field(default_factory=dict)
    states: list[dict[str, Any]] = Field(default_factory=list)
    previous_states: dict[str, dict[str, Any]] = Field(default_factory=dict)
    allowed_entities: list[str] = Field(default_factory=list)
    allowed_scenes: list[str] = Field(default_factory=list)
    allowed_scripts: list[str] = Field(default_factory=list)
    target_capabilities: dict[str, dict[str, Any]] = Field(default_factory=dict)


ScheduledJobStatus = Literal["pending", "completed", "failed", "cancelled"]


class ScheduledJobResponse(BaseModel):
    job_id: str
    text: str
    actions: list[Intent]
    rationale: str | None = None
    schedule: ScheduleSpec
    due_at: datetime
    created_at: datetime
    status: ScheduledJobStatus
    executed_at: datetime | None = None
    cancelled_at: datetime | None = None
    error: str | None = None


ActivityEntryKind = Literal["command", "scheduled_job", "routine"]
ActivityEntryStatus = Literal[
    "executed",
    "dry_run",
    "scheduled",
    "routine_created",
    "saved_scene_created",
    "failed",
]


class ActivityEntryResponse(BaseModel):
    occurred_at: datetime
    kind: ActivityEntryKind
    source: str
    text: str
    dry_run: bool = False
    status: ActivityEntryStatus
    actions: list[Intent] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class ActivityListResponse(BaseModel):
    count: int
    entries: list[ActivityEntryResponse]


class AssistGuardStateResponse(BaseModel):
    enabled: bool
    state: dict[str, Any]


class SavedSceneActivateRequest(BaseModel):
    dry_run: bool = False
    source: str | None = Field(default=None, max_length=80)


class ScheduledJobListResponse(BaseModel):
    count: int
    jobs: list[ScheduledJobResponse]


RoutineStatus = Literal["enabled", "disabled", "deleted"]


class RoutineResponse(BaseModel):
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
    status: RoutineStatus
    error: str | None = None


class RoutineListResponse(BaseModel):
    count: int
    routines: list[RoutineResponse]


class RoutineUpdateRequest(BaseModel):
    time: str = Field(pattern=r"^\d{2}:\d{2}$")

    @model_validator(mode="after")
    def _validate_time(self) -> "RoutineUpdateRequest":
        hour_text, minute_text = self.time.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
        if hour > 23 or minute > 59:
            raise ValueError("Routine time must be a valid HH:MM clock time.")
        return self


SavedSceneStatus = Literal["active", "deleted"]


class SavedSceneResponse(BaseModel):
    scene_id: str
    text: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    actions: list[Intent]
    rationale: str | None = None
    created_at: datetime
    updated_at: datetime
    status: SavedSceneStatus


class SavedSceneListResponse(BaseModel):
    count: int
    scenes: list[SavedSceneResponse]
