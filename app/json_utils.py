from __future__ import annotations

import json
import re
from typing import Any


FENCED_JSON_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.IGNORECASE | re.DOTALL)


def parse_json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object, tolerating occasional markdown fences or extra prose."""
    normalized = text.strip()
    candidates = [normalized]

    fence_match = FENCED_JSON_RE.match(normalized)
    if fence_match:
        candidates.insert(0, fence_match.group(1).strip())

    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload

    decoder = json.JSONDecoder()
    for candidate in candidates:
        for match in re.finditer(r"\{", candidate):
            try:
                payload, _ = decoder.raw_decode(candidate[match.start():])
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload

    raise ValueError("No JSON object found.")
