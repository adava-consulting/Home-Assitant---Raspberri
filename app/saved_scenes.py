from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from app.errors import BridgeError
from app.models import ActionPlan, Intent, SavedSceneResponse, SavedSceneStatus
from app.persistence import load_json_file_with_backup, write_json_file_atomic


logger = logging.getLogger(__name__)


class SavedScene(BaseModel):
    scene_id: str
    text: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    actions: list[Intent]
    rationale: str | None = None
    created_at: datetime
    updated_at: datetime
    status: SavedSceneStatus = "active"


class SavedSceneService:
    def __init__(self, settings: Any):
        self._settings = settings
        self._timezone = ZoneInfo(settings.local_timezone)
        self._enabled = bool(getattr(settings, "saved_scenes_enabled", True))
        self._store_path = Path(settings.saved_scenes_store_path)
        self._lock = asyncio.Lock()
        self._scenes: dict[str, SavedScene] = {}

    async def start(self) -> None:
        if not self._enabled:
            logger.info("Saved scenes disabled.")
            return

        self._store_path.parent.mkdir(parents=True, exist_ok=True)
        await self._load_scenes()
        logger.info("Saved scene service loaded with %s active scene(s).", self.active_count)

    async def stop(self) -> None:
        return

    @property
    def active_count(self) -> int:
        return sum(1 for scene in self._scenes.values() if scene.status == "active")

    async def create_scene(self, text: str, plan: ActionPlan) -> str:
        if not self._enabled:
            raise BridgeError("Saved scenes are disabled.")
        if plan.saved_scene is None:
            raise BridgeError("Cannot create a saved scene without a saved_scene spec.")
        if not plan.actions:
            raise BridgeError("Cannot create a saved scene without actions.")

        now = datetime.now(self._timezone)
        name = plan.saved_scene.name.strip()
        aliases = self._scene_aliases(name, plan.saved_scene.aliases)
        existing_scene = await self._find_scene_by_name(name)

        if existing_scene is None:
            scene = SavedScene(
                scene_id=uuid4().hex,
                text=text,
                name=name,
                aliases=aliases,
                actions=plan.actions,
                rationale=plan.rationale,
                created_at=now,
                updated_at=now,
            )
        else:
            scene = existing_scene.model_copy(
                update={
                    "text": text,
                    "name": name,
                    "aliases": aliases,
                    "actions": plan.actions,
                    "rationale": plan.rationale,
                    "updated_at": now,
                    "status": "active",
                }
            )

        async with self._lock:
            self._scenes[scene.scene_id] = scene
            await self._save_scenes()

        logger.info(
            "Saved scene %s (%s) with %s action(s).",
            scene.scene_id,
            scene.name,
            len(scene.actions),
        )
        return scene.scene_id

    async def list_scenes(self, status: SavedSceneStatus | None = None) -> list[SavedSceneResponse]:
        async with self._lock:
            scenes = list(self._scenes.values())

        if status is not None:
            scenes = [scene for scene in scenes if scene.status == status]
        else:
            scenes = [scene for scene in scenes if scene.status != "deleted"]

        scenes.sort(key=lambda scene: (scene.status != "active", scene.name.lower()))
        return [SavedSceneResponse.model_validate(scene.model_dump()) for scene in scenes]

    async def get_scene(self, scene_id: str) -> SavedSceneResponse:
        async with self._lock:
            scene = self._scenes.get(scene_id)

        if scene is None or scene.status == "deleted":
            raise BridgeError(f"Saved scene not found: {scene_id}")
        return SavedSceneResponse.model_validate(scene.model_dump())

    async def delete_scene(self, scene_id: str) -> SavedSceneResponse:
        async with self._lock:
            scene = self._scenes.get(scene_id)
            if scene is None or scene.status == "deleted":
                raise BridgeError(f"Saved scene not found: {scene_id}")

            scene = scene.model_copy(
                update={
                    "status": "deleted",
                    "updated_at": datetime.now(self._timezone),
                }
            )
            self._scenes[scene_id] = scene
            await self._save_scenes()

        logger.info("Deleted saved scene %s", scene_id)
        return SavedSceneResponse.model_validate(scene.model_dump())

    async def match_scene_request(self, text: str) -> SavedSceneResponse | None:
        if not self._enabled:
            return None
        if self._looks_like_scene_management_request(text):
            return None

        normalized_text = _normalize(text)
        if not normalized_text:
            return None

        async with self._lock:
            active_scenes = [scene for scene in self._scenes.values() if scene.status == "active"]

        best_scene: SavedScene | None = None
        best_score = 0
        for scene in active_scenes:
            score = self._scene_match_score(normalized_text, scene)
            if score > best_score:
                best_scene = scene
                best_score = score

        if best_scene is None:
            return None
        return SavedSceneResponse.model_validate(best_scene.model_dump())

    async def _find_scene_by_name(self, name: str) -> SavedScene | None:
        normalized_name = _normalize(name)
        async with self._lock:
            for scene in self._scenes.values():
                if scene.status == "deleted":
                    continue
                if _normalize(scene.name) == normalized_name:
                    return scene
        return None

    async def _load_scenes(self) -> None:
        data = await asyncio.to_thread(
            load_json_file_with_backup,
            self._store_path,
            [],
            logger=logger,
            label="Saved scene store",
        )
        if not isinstance(data, list):
            logger.warning("Saved scene store payload was not a list. Ignoring it.")
            return

        scenes: dict[str, SavedScene] = {}
        for scene_data in data:
            try:
                scene = SavedScene.model_validate(scene_data)
            except Exception as exc:
                logger.warning("Skipping invalid saved scene record: %s", exc)
                continue
            scenes[scene.scene_id] = scene
        self._scenes = scenes

    async def _save_scenes(self) -> None:
        payload = [scene.model_dump(mode="json") for scene in self._scenes.values()]
        await asyncio.to_thread(write_json_file_atomic, self._store_path, payload)

    def _scene_aliases(self, name: str, aliases: list[str]) -> list[str]:
        candidates = [name, *aliases]
        normalized_aliases: list[str] = []
        seen = set()
        for alias in candidates:
            normalized_alias = " ".join(str(alias).split())
            if not normalized_alias:
                continue
            lowered = normalized_alias.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            normalized_aliases.append(normalized_alias)
        return normalized_aliases

    def _looks_like_scene_management_request(self, text: str) -> bool:
        normalized_text = _normalize(text)
        management_patterns = (
            r"\b(?:create|make|save|set\s+up|add)\s+(?:a\s+)?(?:saved\s+)?scene\b",
            r"\b(?:delete|remove|forget)\s+(?:the\s+)?(?:saved\s+)?scene\b",
            r"\b(?:list|show)\s+(?:my\s+)?(?:saved\s+)?scenes\b",
            r"\b(?:crea|crear|guarda|guardar|borra|borrar|elimina|eliminar)\b.*\bescena\b",
        )
        return any(re.search(pattern, normalized_text) for pattern in management_patterns)

    def _scene_match_score(self, normalized_text: str, scene: SavedScene) -> int:
        best_score = 0
        activation_words = {
            "activate",
            "run",
            "start",
            "use",
            "turn on",
            "apply",
            "enable",
            "set",
            "modo",
            "activa",
            "activar",
            "usa",
            "usar",
        }
        has_activation_word = any(_contains_phrase(normalized_text, word) for word in activation_words)

        for alias in scene.aliases:
            normalized_alias = _normalize(alias)
            if not normalized_alias:
                continue
            if normalized_text == normalized_alias:
                best_score = max(best_score, 200 + len(normalized_alias))
            elif has_activation_word and _contains_phrase(normalized_text, normalized_alias):
                best_score = max(best_score, 100 + len(normalized_alias))
        return best_score


def _normalize(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text.lower())
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    normalized = re.sub(r"[^a-z0-9\s]", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _contains_phrase(text: str, phrase: str) -> bool:
    escaped = re.escape(phrase.strip())
    return bool(escaped and re.search(rf"\b{escaped}\b", text))
