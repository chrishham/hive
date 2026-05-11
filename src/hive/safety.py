# src/hive/safety.py
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

SESSION_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_REPLACE_RE = re.compile(r"[^A-Za-z0-9._-]")
_MAX_LEN = 64
_DEFAULT_NAME = "session"


class InvalidSessionName(ValueError):
    pass


class TmuxError(RuntimeError):
    pass


def validate_session_name(name: str) -> None:
    if not isinstance(name, str) or not SESSION_NAME_RE.fullmatch(name):
        raise InvalidSessionName(f"invalid session name: {name!r}")
    if name in {".", ".."}:
        raise InvalidSessionName(f"reserved session name: {name!r}")


def sanitize_session_name(name: str) -> str:
    if not isinstance(name, str):
        return _DEFAULT_NAME
    cleaned = _REPLACE_RE.sub("_", name)[:_MAX_LEN]
    if not cleaned or cleaned in {".", ".."} or set(cleaned) == {"_"}:
        return _DEFAULT_NAME
    return cleaned


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def escape_tmux_format(text: str) -> str:
    return text.replace("#", "##")
