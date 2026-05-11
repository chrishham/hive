from __future__ import annotations

import json
from pathlib import Path

from hive.detector import SessionState

_STATE_VALUES = {s.value: s for s in SessionState}


def state_dir() -> Path:
    return Path.home() / ".claude" / "hive" / "state"


def state_file_path(name: str) -> Path:
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise ValueError(f"unsafe session name: {name!r}")
    return state_dir() / f"{name}.json"


def read_session_state(name: str) -> SessionState | None:
    state, _ = read_session_state_with_meta(name)
    return state


def read_session_state_with_meta(
    name: str,
) -> tuple[SessionState | None, str | None]:
    try:
        path = state_file_path(name)
    except ValueError:
        return None, None
    if not path.exists():
        return None, None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None, None
    if not isinstance(data, dict):
        return None, None
    raw = data.get("state")
    state = _STATE_VALUES.get(raw) if isinstance(raw, str) else None
    updated_at = data.get("updated_at")
    if not isinstance(updated_at, str):
        updated_at = None
    return state, updated_at


def read_session_id(name: str) -> str | None:
    try:
        path = state_file_path(name)
    except ValueError:
        return None
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    sid = data.get("session_id")
    return sid if isinstance(sid, str) and sid else None


def remove_session_state(name: str) -> None:
    try:
        state_file_path(name).unlink(missing_ok=True)
    except ValueError:
        return
