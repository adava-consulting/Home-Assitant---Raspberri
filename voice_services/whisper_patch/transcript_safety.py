from __future__ import annotations

import re


_NORMALIZE_PATTERN = re.compile(r"[^a-z0-9\s]")
_WHITESPACE_PATTERN = re.compile(r"\s+")


def normalize_transcript_text(text: str) -> str:
    collapsed = _WHITESPACE_PATTERN.sub(" ", str(text or "").strip())
    return collapsed.strip()


def _normalized_text_key(text: str) -> str:
    normalized = normalize_transcript_text(text).lower()
    normalized = _NORMALIZE_PATTERN.sub(" ", normalized)
    normalized = _WHITESPACE_PATTERN.sub(" ", normalized)
    return normalized.strip()


def _collapse_repeated_word_spans(text: str) -> str:
    words = normalize_transcript_text(text).split()
    if len(words) < 6:
        return normalize_transcript_text(text)

    changed = True
    while changed:
        changed = False
        max_span = min(12, len(words) // 2)
        for span in range(max_span, 2, -1):
            i = 0
            while i + (2 * span) <= len(words):
                if words[i : i + span] == words[i + span : i + (2 * span)]:
                    del words[i + span : i + (2 * span)]
                    changed = True
                    break
                i += 1
            if changed:
                break

    return " ".join(words)


def looks_like_repetition_loop(text: str) -> bool:
    words = _normalized_text_key(text).split()
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


def _extract_repeated_phrase_once(text: str) -> str | None:
    words = _normalized_text_key(text).split()
    if len(words) < 6:
        return None

    max_phrase_len = min(8, len(words) // 2)
    for phrase_len in range(max_phrase_len, 1, -1):
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
                return " ".join(phrase)

    return None


def sanitize_transcript_text(text: str, *, max_chars: int) -> str:
    normalized = normalize_transcript_text(text)
    if not normalized:
        return ""

    if looks_like_repetition_loop(normalized):
        return ""

    sanitized = _collapse_repeated_word_spans(normalized)

    if max_chars > 0 and len(sanitized) > max_chars:
        trimmed = sanitized[:max_chars].rstrip(" ,;:-")
        if " " in trimmed:
            trimmed = trimmed.rsplit(" ", 1)[0]
        sanitized = trimmed or sanitized[:max_chars]

    return sanitized
