from __future__ import annotations

import json
from pathlib import Path

from hive.detector import SessionState

_STATE_VALUES = {s.value: s for s in SessionState}


def state_dir() -> Path:
    return Path.home() / ".claude" / "hive" / "state"


def state_file_path(name: str) -> Path:
    return state_dir() / f"{name}.json"


def read_session_state(name: str) -> SessionState | None:
    path = state_file_path(name)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    raw = data.get("state") if isinstance(data, dict) else None
    if not isinstance(raw, str):
        return None
    return _STATE_VALUES.get(raw)


def remove_session_state(name: str) -> None:
    state_file_path(name).unlink(missing_ok=True)
