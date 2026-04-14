from __future__ import annotations

from collections.abc import Mapping
import ipaddress
import secrets
from typing import Any


def request_has_valid_bridge_access(
    settings: Any,
    headers: Mapping[str, str] | None = None,
    client_host: str | None = None,
) -> bool:
    expected_token = str(getattr(settings, "command_bridge_api_token", "")).strip()
    if not expected_token:
        return True

    if _is_loopback_client(client_host):
        return True

    normalized_headers = {
        str(key).lower(): str(value).strip()
        for key, value in (headers or {}).items()
    }
    configured_header = str(
        getattr(settings, "command_bridge_api_header_name", "X-Bridge-Token")
    ).strip().lower()

    candidates: list[str] = []
    if configured_header:
        header_value = normalized_headers.get(configured_header, "")
        if header_value:
            candidates.append(header_value)

    authorization = normalized_headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        bearer_token = authorization[7:].strip()
        if bearer_token:
            candidates.append(bearer_token)

    return any(secrets.compare_digest(candidate, expected_token) for candidate in candidates)


def _is_loopback_client(client_host: str | None) -> bool:
    if not client_host:
        return False
    if client_host == "testclient":
        return True

    try:
        return ipaddress.ip_address(client_host).is_loopback
    except ValueError:
        return client_host in {"localhost"}
