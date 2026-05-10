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


CONTEXT_WINDOWS = {
    "claude-opus-4-7": 200_000,
    "claude-opus-4-6": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-haiku-4-5": 200_000,
}


def derive_context_pct(jsonl_path: Path, model: str | None) -> int | None:
    """Walk JSONL; find latest assistant message with `usage`; compute pct."""
    if model is None or model not in CONTEXT_WINDOWS:
        return None
    last_usage = None
    try:
        with jsonl_path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") != "assistant":
                    continue
                usage = (ev.get("message") or {}).get("usage")
                if usage:
                    last_usage = usage
    except OSError:
        return None
    if not last_usage:
        return None
    total = (
        last_usage.get("input_tokens", 0)
        + last_usage.get("cache_creation_input_tokens", 0)
        + last_usage.get("cache_read_input_tokens", 0)
        + last_usage.get("output_tokens", 0)
    )
    window = CONTEXT_WINDOWS[model]
    return min(round(total / window * 100), 100)
