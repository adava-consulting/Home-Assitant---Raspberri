from __future__ import annotations

import logging

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.auth import request_has_valid_bridge_access
from app.assist_guard import AssistGuardService
from app.config import Settings, get_settings
from app.audio_output import AudioOutputService
from app.activity_log import ActivityLogService
from app.errors import BridgeError, UpstreamServiceError, ValidationError
from app.health import build_health_payload
from app.home_assistant import HomeAssistantClient
from app.interpreter_factory import build_interpreter
from app.monitor_control import MonitorControlService
from app.models import (
    ActivityListResponse,
    AssistCommandRequest,
    AssistGuardStateResponse,
    CommandRequest,
    CommandResponse,
    RoutineListResponse,
    RoutineResponse,
    RoutineUpdateRequest,
    SavedSceneActivateRequest,
    SavedSceneListResponse,
    SavedSceneResponse,
    ScheduledJobListResponse,
    ScheduledJobResponse,
)
from app.orchestrator import CommandOrchestrator
from app.routines import RoutineService
from app.saved_scenes import SavedSceneService
from app.scheduler import SchedulerService
from app.state_memory import PreviousStateMemoryService


logger = logging.getLogger(__name__)


def _configure_logging(settings: Settings) -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


async def require_bridge_auth(request: Request) -> None:
    settings = getattr(request.app.state, "settings", None) or get_settings()
    client_host = request.client.host if request.client is not None else None
    if request_has_valid_bridge_access(settings, request.headers, client_host):
        return

    raise HTTPException(status_code=401, detail="Unauthorized")


app = FastAPI(
    title="Home Assistant Command Bridge",
    version="0.1.0",
    dependencies=[Depends(require_bridge_auth)],
)


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    detail = "I had trouble understanding that command. Please try again."
    errors = exc.errors()
    if any(
        error.get("loc") == ("body", "text") and error.get("type") == "string_too_long"
        for error in errors
    ):
        detail = "The spoken command was too long or repeated. Please try again."
    return JSONResponse(status_code=422, content={"detail": detail})


@app.on_event("startup")
async def startup_event() -> None:
    settings = get_settings()
    _configure_logging(settings)
    app.state.settings = settings
    app.state.monitor_control = MonitorControlService(settings)
    app.state.home_assistant = HomeAssistantClient(
        settings,
        local_script_service=app.state.monitor_control,
    )
    await app.state.home_assistant.start()
    app.state.interpreter_bundle = build_interpreter(settings)
    app.state.audio_output = AudioOutputService(settings)
    await app.state.audio_output.start()
    app.state.assist_guard = AssistGuardService(settings)
    await app.state.assist_guard.start()
    app.state.activity_log = ActivityLogService(settings)
    await app.state.activity_log.start()
    app.state.state_memory = PreviousStateMemoryService(settings, app.state.home_assistant)
    await app.state.state_memory.start()
    app.state.scheduler = SchedulerService(
        settings,
        app.state.home_assistant,
        state_memory=app.state.state_memory,
        activity_log=app.state.activity_log,
    )
    await app.state.scheduler.start()
    app.state.routines = RoutineService(
        settings,
        app.state.home_assistant,
        state_memory=app.state.state_memory,
        activity_log=app.state.activity_log,
    )
    await app.state.routines.start()
    app.state.saved_scenes = SavedSceneService(settings)
    await app.state.saved_scenes.start()


@app.on_event("shutdown")
async def shutdown_event() -> None:
    saved_scenes = getattr(app.state, "saved_scenes", None)
    if saved_scenes is not None:
        await saved_scenes.stop()
    routines = getattr(app.state, "routines", None)
    if routines is not None:
        await routines.stop()
    scheduler = getattr(app.state, "scheduler", None)
    if scheduler is not None:
        await scheduler.stop()
    state_memory = getattr(app.state, "state_memory", None)
    if state_memory is not None:
        await state_memory.stop()
    activity_log = getattr(app.state, "activity_log", None)
    if activity_log is not None:
        await activity_log.stop()
    assist_guard = getattr(app.state, "assist_guard", None)
    if assist_guard is not None:
        await assist_guard.stop()
    home_assistant = getattr(app.state, "home_assistant", None)
    if home_assistant is not None:
        await home_assistant.stop()
    audio_output = getattr(app.state, "audio_output", None)
    if audio_output is not None:
        await audio_output.stop()


def get_orchestrator(request: Request) -> CommandOrchestrator:
    return CommandOrchestrator(
        request.app.state.settings,
        request.app.state.home_assistant,
        request.app.state.interpreter_bundle.interpreter,
        scheduler=request.app.state.scheduler,
        routines=request.app.state.routines,
        saved_scenes=request.app.state.saved_scenes,
        state_memory=request.app.state.state_memory,
        audio_output=request.app.state.audio_output,
        activity_log=request.app.state.activity_log,
        assist_guard=request.app.state.assist_guard,
    )


@app.get("/health")
async def health(request: Request) -> dict[str, object]:
    interpreter_bundle = request.app.state.interpreter_bundle
    return await build_health_payload(
        settings=request.app.state.settings,
        interpreter_name=interpreter_bundle.name,
        scheduler=request.app.state.scheduler,
        routines=request.app.state.routines,
        saved_scenes=request.app.state.saved_scenes,
        home_assistant=request.app.state.home_assistant,
        audio_output=request.app.state.audio_output,
    )


@app.post("/commands/interpret", response_model=CommandResponse)
async def interpret_command(
    request: CommandRequest,
    orchestrator: CommandOrchestrator = Depends(get_orchestrator),
) -> CommandResponse:
    try:
        return await orchestrator.process(
            request.text,
            request.dry_run,
            source=request.source,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except UpstreamServiceError as exc:
        logger.warning("Upstream service failure while processing command: %s", exc)
        raise HTTPException(
            status_code=502,
            detail="I couldn't complete that in Home Assistant. Please try again.",
        ) from exc
    except BridgeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/commands/assist", response_model=CommandResponse)
async def interpret_assist_command(
    request: AssistCommandRequest,
    orchestrator: CommandOrchestrator = Depends(get_orchestrator),
) -> CommandResponse:
    assist_source = " ".join(str(request.source or "assist_conversation").split()).lower()
    if not assist_source.startswith("assist_"):
        raise HTTPException(status_code=400, detail="Assist commands must use an assist_* source.")

    try:
        return await orchestrator.process(
            request.text,
            request.dry_run,
            source=assist_source,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except UpstreamServiceError as exc:
        logger.warning("Upstream service failure while processing assist command: %s", exc)
        raise HTTPException(
            status_code=502,
            detail="I couldn't complete that in Home Assistant. Please try again.",
        ) from exc
    except BridgeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/activity", response_model=ActivityListResponse)
async def list_activity(
    request: Request,
    limit: int = Query(default=20, ge=1, le=200),
) -> ActivityListResponse:
    activity_log = request.app.state.activity_log
    entries = await activity_log.list_entries(limit=limit)
    return ActivityListResponse(count=len(entries), entries=entries)


@app.get("/assist-guard", response_model=AssistGuardStateResponse)
async def get_assist_guard_state(request: Request) -> AssistGuardStateResponse:
    assist_guard = request.app.state.assist_guard
    if assist_guard is None:
        return AssistGuardStateResponse(enabled=False, state={})
    state = await assist_guard.get_state()
    return AssistGuardStateResponse(enabled=True, state=state)


@app.get("/scheduled-jobs", response_model=ScheduledJobListResponse)
async def list_scheduled_jobs(
    request: Request,
    status: str | None = Query(default=None),
) -> ScheduledJobListResponse:
    scheduler = request.app.state.scheduler
    allowed_statuses = {"pending", "completed", "failed", "cancelled"}
    if status is not None and status not in allowed_statuses:
        raise HTTPException(status_code=400, detail=f"Unsupported status filter: {status}")

    jobs = await scheduler.list_jobs(status=status)
    return ScheduledJobListResponse(count=len(jobs), jobs=jobs)


@app.post("/scheduled-jobs/{job_id}/cancel", response_model=ScheduledJobResponse)
async def cancel_scheduled_job(job_id: str, request: Request) -> ScheduledJobResponse:
    scheduler = request.app.state.scheduler
    try:
        return await scheduler.cancel_job(job_id)
    except BridgeError as exc:
        message = str(exc)
        if "not found" in message.lower():
            raise HTTPException(status_code=404, detail=message) from exc
        raise HTTPException(status_code=409, detail=message) from exc


@app.get("/routines", response_model=RoutineListResponse)
async def list_routines(
    request: Request,
    status: str | None = Query(default=None),
) -> RoutineListResponse:
    routines = request.app.state.routines
    allowed_statuses = {"enabled", "disabled", "deleted"}
    if status is not None and status not in allowed_statuses:
        raise HTTPException(status_code=400, detail=f"Unsupported status filter: {status}")

    routine_list = await routines.list_routines(status=status)
    return RoutineListResponse(count=len(routine_list), routines=routine_list)


@app.post("/routines/{routine_id}/disable", response_model=RoutineResponse)
async def disable_routine(routine_id: str, request: Request) -> RoutineResponse:
    routines = request.app.state.routines
    try:
        return await routines.disable_routine(routine_id)
    except BridgeError as exc:
        message = str(exc)
        if "not found" in message.lower():
            raise HTTPException(status_code=404, detail=message) from exc
        raise HTTPException(status_code=409, detail=message) from exc


@app.post("/routines/{routine_id}/enable", response_model=RoutineResponse)
async def enable_routine(routine_id: str, request: Request) -> RoutineResponse:
    routines = request.app.state.routines
    try:
        return await routines.enable_routine(routine_id)
    except BridgeError as exc:
        message = str(exc)
        if "not found" in message.lower():
            raise HTTPException(status_code=404, detail=message) from exc
        raise HTTPException(status_code=409, detail=message) from exc


@app.post("/routines/{routine_id}/time", response_model=RoutineResponse)
async def update_routine_time(
    routine_id: str,
    update: RoutineUpdateRequest,
    request: Request,
) -> RoutineResponse:
    routines = request.app.state.routines
    try:
        return await routines.update_routine_time(routine_id, update.time)
    except BridgeError as exc:
        message = str(exc)
        if "not found" in message.lower():
            raise HTTPException(status_code=404, detail=message) from exc
        raise HTTPException(status_code=409, detail=message) from exc


@app.delete("/routines/{routine_id}", response_model=RoutineResponse)
async def delete_routine(routine_id: str, request: Request) -> RoutineResponse:
    routines = request.app.state.routines
    try:
        return await routines.delete_routine(routine_id)
    except BridgeError as exc:
        message = str(exc)
        if "not found" in message.lower():
            raise HTTPException(status_code=404, detail=message) from exc
        raise HTTPException(status_code=409, detail=message) from exc


@app.get("/saved-scenes", response_model=SavedSceneListResponse)
async def list_saved_scenes(
    request: Request,
    status: str | None = Query(default=None),
) -> SavedSceneListResponse:
    saved_scenes = request.app.state.saved_scenes
    allowed_statuses = {"active", "deleted"}
    if status is not None and status not in allowed_statuses:
        raise HTTPException(status_code=400, detail=f"Unsupported status filter: {status}")

    scene_list = await saved_scenes.list_scenes(status=status)
    return SavedSceneListResponse(count=len(scene_list), scenes=scene_list)


@app.post("/saved-scenes/{scene_id}/activate", response_model=CommandResponse)
async def activate_saved_scene(
    scene_id: str,
    request: Request,
    activation: SavedSceneActivateRequest | None = None,
) -> CommandResponse:
    saved_scenes = request.app.state.saved_scenes
    try:
        scene = await saved_scenes.get_scene(scene_id)
    except BridgeError as exc:
        message = str(exc)
        if "not found" in message.lower():
            raise HTTPException(status_code=404, detail=message) from exc
        raise HTTPException(status_code=409, detail=message) from exc

    orchestrator = get_orchestrator(request)
    return await orchestrator.activate_saved_scene(
        scene,
        text=scene.name,
        dry_run=activation.dry_run if activation is not None else False,
        source=activation.source if activation is not None else None,
    )


@app.delete("/saved-scenes/{scene_id}", response_model=SavedSceneResponse)
async def delete_saved_scene(scene_id: str, request: Request) -> SavedSceneResponse:
    saved_scenes = request.app.state.saved_scenes
    try:
        return await saved_scenes.delete_scene(scene_id)
    except BridgeError as exc:
        message = str(exc)
        if "not found" in message.lower():
            raise HTTPException(status_code=404, detail=message) from exc
        raise HTTPException(status_code=409, detail=message) from exc
