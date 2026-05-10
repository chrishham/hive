import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from hive.derive import (
    STALE_AFTER,
    derive_state,
    read_last_conversation_event,
)
from hive.session import SessionState


def write_jsonl(path: Path, events: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")


NOW = datetime(2026, 5, 10, 12, 0, 0, tzinfo=timezone.utc)


def evt(type_, ts=NOW, **kw):
    out = {"type": type_, "timestamp": ts.isoformat()}
    out.update(kw)
    return out


# --- read_last_conversation_event ---

def test_read_returns_none_for_empty_file(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text("")
    assert read_last_conversation_event(p) is None


def test_read_skips_metadata_types(tmp_path):
    p = tmp_path / "x.jsonl"
    write_jsonl(p, [
        evt("user", message={"content": [{"type": "text"}]}),
        evt("permission-mode"),
        evt("custom-title"),
        evt("system"),
        evt("attachment"),
        evt("file-history-snapshot"),
        evt("agent-name"),
        evt("last-prompt"),
    ])
    last = read_last_conversation_event(p)
    assert last["type"] == "user"


def test_read_returns_last_user_or_assistant(tmp_path):
    p = tmp_path / "x.jsonl"
    write_jsonl(p, [
        evt("user"),
        evt("assistant", message={"stop_reason": "end_turn"}),
        evt("permission-mode"),
    ])
    last = read_last_conversation_event(p)
    assert last["type"] == "assistant"
    assert last["message"]["stop_reason"] == "end_turn"


def test_read_tolerates_malformed_lines(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text(
        "this is not json\n"
        + json.dumps(evt("assistant", message={"stop_reason": "end_turn"})) + "\n"
        + "trailing garbage\n"
    )
    last = read_last_conversation_event(p)
    assert last["type"] == "assistant"


# --- derive_state ---

def test_state_bootstrapping_when_no_events(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text("")
    assert derive_state(p, NOW) == SessionState.BOOTSTRAPPING


def test_state_waiting_when_end_turn(tmp_path):
    p = tmp_path / "x.jsonl"
    write_jsonl(p, [evt("assistant", message={"stop_reason": "end_turn"})])
    assert derive_state(p, NOW) == SessionState.WAITING


def test_state_working_when_tool_use(tmp_path):
    p = tmp_path / "x.jsonl"
    write_jsonl(p, [evt("assistant", message={"stop_reason": "tool_use"})])
    assert derive_state(p, NOW) == SessionState.WORKING


def test_state_working_when_user_pending(tmp_path):
    p = tmp_path / "x.jsonl"
    write_jsonl(p, [evt("user", message={"content": [{"type": "text"}]})])
    assert derive_state(p, NOW) == SessionState.WORKING


def test_state_working_when_user_tool_result(tmp_path):
    p = tmp_path / "x.jsonl"
    write_jsonl(p, [evt("user", message={"content": [{"type": "tool_result"}]})])
    assert derive_state(p, NOW) == SessionState.WORKING


def test_state_waiting_when_stale(tmp_path):
    p = tmp_path / "x.jsonl"
    write_jsonl(p, [evt("assistant",
                        ts=NOW - STALE_AFTER - timedelta(seconds=1),
                        message={"stop_reason": "tool_use"})])
    assert derive_state(p, NOW) == SessionState.WAITING


def test_state_working_when_no_stop_reason(tmp_path):
    p = tmp_path / "x.jsonl"
    write_jsonl(p, [evt("assistant", message={"content": [{"type": "text"}]})])
    assert derive_state(p, NOW) == SessionState.WORKING
