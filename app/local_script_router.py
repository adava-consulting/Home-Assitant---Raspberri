from __future__ import annotations

from typing import Any

from app.errors import UpstreamServiceError
from app.models import Intent


class LocalScriptRouterService:
    def __init__(self, services: list[Any] | tuple[Any, ...]):
        self._services = [service for service in services if service is not None]

    def can_handle(self, intent: Intent) -> bool:
        return any(service.can_handle(intent) for service in self._services)

    async def execute_intent(self, intent: Intent) -> dict[str, Any]:
        for service in self._services:
            if service.can_handle(intent):
                return await service.execute_intent(intent)

        raise UpstreamServiceError(f"No local script handler is configured for {intent.target}.")
