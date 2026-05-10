from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from hive.session import SessionState

CONVERSATION_TYPES = {"user", "assistant"}
STALE_AFTER = timedelta(minutes=5)
TAIL_CHUNK_BYTES = 8192


def read_last_conversation_event(path: Path) -> dict | None:
    """Read the file backward in 8KB chunks; return last user/assistant event."""
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return None
    if size == 0:
        return None

    buf = b""
    pos = size
    with path.open("rb") as f:
        while pos > 0:
            read_size = min(TAIL_CHUNK_BYTES, pos)
            pos -= read_size
            f.seek(pos)
            buf = f.read(read_size) + buf
            lines = buf.split(b"\n")
            buf = lines[0]  # keep partial line for next iteration
            for line in reversed(lines[1:]):
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") in CONVERSATION_TYPES:
                    return ev
        # Process any remaining first-chunk content
        line = buf.strip()
        if line:
            try:
                ev = json.loads(line)
                if ev.get("type") in CONVERSATION_TYPES:
                    return ev
            except json.JSONDecodeError:
                pass
    return None


def derive_state(jsonl_path: Path, now: datetime) -> SessionState:
    last = read_last_conversation_event(jsonl_path)
    if last is None:
        return SessionState.BOOTSTRAPPING

    ts = datetime.fromisoformat(last["timestamp"].replace("Z", "+00:00"))
    if now - ts > STALE_AFTER:
        return SessionState.WAITING

    msg = last.get("message") or {}
    stop = msg.get("stop_reason")
    if last["type"] == "assistant":
        if stop == "end_turn":
            return SessionState.WAITING
        return SessionState.WORKING
    return SessionState.WORKING  # type == "user"
