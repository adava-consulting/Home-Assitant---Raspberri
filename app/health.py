from __future__ import annotations

from typing import Any


async def build_health_payload(
    *,
    settings: Any,
    interpreter_name: str,
    scheduler: Any,
    routines: Any,
    saved_scenes: Any,
    home_assistant: Any,
    audio_output: Any,
) -> dict[str, Any]:
    monitored_entities = list(getattr(settings, "health_monitored_entities", []))
    ha_snapshot = await home_assistant.build_health_snapshot(monitored_entities)
    monitored_payload = ha_snapshot.get("monitored_entities", [])

    degraded = not bool(ha_snapshot.get("reachable", False))
    if not degraded:
        degraded = any(
            isinstance(entity, dict) and entity.get("status") != "ok"
            for entity in monitored_payload
        )

    return {
        "status": "degraded" if degraded else "ok",
        "interpreter": interpreter_name,
        "interpreter_mode": getattr(settings, "normalized_interpreter_mode", interpreter_name),
        "fast_path_local_first": bool(getattr(settings, "fast_path_local_first", False)),
        "scheduled_jobs": str(getattr(scheduler, "pending_count", 0)),
        "routines": str(getattr(routines, "enabled_count", 0)),
        "saved_scenes": str(getattr(saved_scenes, "active_count", 0)),
        "voice_model_loaded": bool(getattr(settings, "voice_model", {})),
        "home_assistant": ha_snapshot,
        "audio_output": audio_output.diagnostics(),
    }
