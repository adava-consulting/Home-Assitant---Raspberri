from __future__ import annotations

import re

from app.errors import ValidationError


_CLAUDE_PREFIX_RE = re.compile(
    r"^\s*claude(?:\s*[:,]\s*|\s+)(?P<request>.+?)\s*$",
    re.IGNORECASE,
)
_CLAUDE_ONLY_RE = re.compile(r"^\s*claude\s*[:,]?\s*$", re.IGNORECASE)


def extract_forced_claude_request(text: str) -> str | None:
    match = _CLAUDE_PREFIX_RE.match(text)
    if match:
        request = match.group("request").strip()
        if request:
            return request

    if _CLAUDE_ONLY_RE.match(text):
        raise ValidationError("Say a request after the Claude prefix.")

    return None
