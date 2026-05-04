from __future__ import annotations

import re


_NORMALIZE_PATTERN = re.compile(r"[^a-z0-9\s]")
_WHITESPACE_PATTERN = re.compile(r"\s+")
_BLOCKLIST_PATTERNS = (
    re.compile(r"^thank you very much$"),
    re.compile(r"^hello ladies and gentlemen$"),
    re.compile(
        r"^if you have any questions please let us know in the comments below(?: if you have any questions)?$"
    ),
    re.compile(r"^we are in the past but we are not in the past$"),
)
_PROMPT_EXAMPLE_KEYS = (
    "turn on the studio lights",
    "turn off the studio lights",
    "turn on the room lights",
    "turn off the room lights",
    "open youtube on the mac",
    "enciende las luces del estudio",
    "apaga las luces del estudio",
    "enciende las luces de room",
    "apaga las luces de room",
    "abre youtube en la mac",
)


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


def looks_like_prompt_leakage(text: str) -> bool:
    normalized_key = _normalized_text_key(text)
    if not normalized_key:
        return False

    matched_examples = {
        example
        for example in _PROMPT_EXAMPLE_KEYS
        if re.search(rf"\b{re.escape(example)}\b", normalized_key)
    }
    return len(matched_examples) >= 2


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

    normalized_key = _normalized_text_key(normalized)
    if any(pattern.match(normalized_key) for pattern in _BLOCKLIST_PATTERNS):
        return ""

    if looks_like_prompt_leakage(normalized):
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
