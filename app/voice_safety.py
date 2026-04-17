from __future__ import annotations

import re


_NORMALIZE_PATTERN = re.compile(r"[^a-z0-9\s]")
_WHITESPACE_PATTERN = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    collapsed = _WHITESPACE_PATTERN.sub(" ", str(text or "").strip())
    return collapsed.strip()


def normalized_text_key(text: str) -> str:
    normalized = normalize_text(text).lower()
    normalized = _NORMALIZE_PATTERN.sub(" ", normalized)
    normalized = _WHITESPACE_PATTERN.sub(" ", normalized)
    return normalized.strip()


def looks_like_repetition_loop(text: str) -> bool:
    words = normalized_text_key(text).split()
    if len(words) < 8:
        return False

    max_phrase_len = min(8, len(words) // 2)
    for phrase_len in range(2, max_phrase_len + 1):
        for start in range(0, len(words) - phrase_len):
            phrase = words[start : start + phrase_len]
            if not phrase:
                continue

            repeats = 1
            cursor = start + phrase_len
            while cursor + phrase_len <= len(words):
                if words[cursor : cursor + phrase_len] != phrase:
                    break
                repeats += 1
                cursor += phrase_len

            if repeats >= 3:
                return True

    unique_ratio = len(set(words)) / max(len(words), 1)
    if len(words) >= 40 and unique_ratio <= 0.35:
        return True

    return False


def sanitize_voice_input(text: str) -> str:
    normalized = normalize_text(text)
    if looks_like_repetition_loop(normalized):
        raise ValueError("repetition_loop")
    return normalized


def sanitize_spoken_response(text: str, *, max_chars: int) -> str:
    normalized = normalize_text(text)
    if not normalized:
        return ""

    lowered = normalized.lower()
    if "string should have at most" in lowered or "validation error" in lowered:
        return "I had trouble understanding that. Please try again."
    if "failed to execute home assistant service" in lowered or "internal server error" in lowered:
        return "I couldn't complete that in Home Assistant. Please try again."

    if looks_like_repetition_loop(normalized):
        return "I had trouble understanding that. Please try again."

    if max_chars > 0 and len(normalized) > max_chars:
        trimmed = normalized[:max_chars].rstrip(" ,;:-")
        if not trimmed.endswith((".", "!", "?")):
            trimmed = f"{trimmed}."
        return trimmed

    return normalized
