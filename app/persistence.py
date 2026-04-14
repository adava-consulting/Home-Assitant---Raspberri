from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import tempfile
from typing import Any


def load_json_file_with_backup(
    path: Path,
    default: Any,
    *,
    logger: logging.Logger | None = None,
    label: str = "JSON store",
) -> Any:
    primary_had_error = False
    errors: list[str] = []

    for role, candidate in (("primary", path), ("backup", _backup_path(path))):
        if not candidate.exists():
            continue

        try:
            raw_payload = candidate.read_text("utf-8")
        except OSError as exc:
            if role == "primary":
                primary_had_error = True
            errors.append(f"{role} read failed: {exc}")
            continue

        if not raw_payload.strip():
            if role == "primary":
                primary_had_error = True
            errors.append(f"{role} file is empty")
            continue

        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError as exc:
            if role == "primary":
                primary_had_error = True
            errors.append(f"{role} JSON decode failed: {exc}")
            continue

        if role == "backup" and primary_had_error:
            try:
                write_json_file_atomic(path, payload)
            except OSError as exc:
                errors.append(f"primary restore from backup failed: {exc}")

        if errors and logger is not None:
            logger.warning("%s recovered using %s file. Details: %s", label, role, "; ".join(errors))

        return payload

    if errors and logger is not None:
        logger.warning("%s could not be loaded. Using default value. Details: %s", label, "; ".join(errors))
    return default


def write_json_file_atomic(path: Path, payload: Any) -> None:
    serialized = json.dumps(payload, ensure_ascii=True, indent=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_text_atomic(path, serialized)
    _write_text_atomic(_backup_path(path), serialized)


def _backup_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.bak")


def _write_text_atomic(path: Path, content: str) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)
