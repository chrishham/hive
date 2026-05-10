import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from hive.session import SessionState
from hive.watcher import StateWatcher


def write_event(path: Path, type_: str, **msg) -> None:
    now = datetime.now(timezone.utc).isoformat()
    ev = {"type": type_, "timestamp": now}
    if msg:
        ev["message"] = msg
    with path.open("a") as f:
        f.write(json.dumps(ev) + "\n")


@pytest.mark.asyncio
async def test_emits_event_on_initial_bind(tmp_path):
    jsonl = tmp_path / "x.jsonl"
    write_event(jsonl, "assistant", stop_reason="end_turn")

    w = StateWatcher(poll_interval=0.05)
    w.start()
    queue = w.subscribe()
    w.bind("sess-1", jsonl)
    try:
        evt = await asyncio.wait_for(queue.get(), timeout=2)
        assert evt == ("sess-1", SessionState.WAITING)
    finally:
        w.stop()


@pytest.mark.asyncio
async def test_emits_event_when_jsonl_updates(tmp_path):
    jsonl = tmp_path / "y.jsonl"
    write_event(jsonl, "assistant", stop_reason="end_turn")

    w = StateWatcher(poll_interval=0.05)
    w.start()
    queue = w.subscribe()
    w.bind("sess-2", jsonl)
    try:
        # Drain initial event
        await asyncio.wait_for(queue.get(), timeout=2)

        # Append a new turn => state should flip to WORKING
        write_event(jsonl, "user", content=[{"type": "text"}])

        evt = await asyncio.wait_for(queue.get(), timeout=2)
        assert evt == ("sess-2", SessionState.WORKING)
    finally:
        w.stop()


@pytest.mark.asyncio
async def test_unbind_stops_emitting(tmp_path):
    jsonl = tmp_path / "z.jsonl"
    write_event(jsonl, "assistant", stop_reason="end_turn")

    w = StateWatcher(poll_interval=0.05)
    w.start()
    queue = w.subscribe()
    w.bind("sess-3", jsonl)
    try:
        await asyncio.wait_for(queue.get(), timeout=2)
        w.unbind("sess-3")
        write_event(jsonl, "user", content=[{"type": "text"}])
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(queue.get(), timeout=0.5)
    finally:
        w.stop()


@pytest.mark.asyncio
async def test_multiple_subscribers_all_receive(tmp_path):
    jsonl = tmp_path / "m.jsonl"
    write_event(jsonl, "assistant", stop_reason="end_turn")

    w = StateWatcher(poll_interval=0.05)
    w.start()
    q1 = w.subscribe()
    q2 = w.subscribe()
    w.bind("sess-m", jsonl)
    try:
        e1 = await asyncio.wait_for(q1.get(), timeout=2)
        e2 = await asyncio.wait_for(q2.get(), timeout=2)
        assert e1 == e2 == ("sess-m", SessionState.WAITING)
    finally:
        w.stop()
