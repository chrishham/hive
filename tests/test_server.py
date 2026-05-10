import asyncio
import sys
import time
from pathlib import Path

import pytest
from httpx import AsyncClient, ASGITransport
from starlette.testclient import TestClient

from hive.server import build_app
from hive.session import SessionState


@pytest.fixture
def fake_claude_cmd():
    fake = Path(__file__).parent / "fake_claude.py"
    return [sys.executable, str(fake.absolute())]


@pytest.fixture
def app(fake_claude_cmd, tmp_path):
    return build_app(
        claude_command=fake_claude_cmd,
        claude_projects_dir=tmp_path / "claude_projects",
        state_path=tmp_path / "state.json",
        static_dir=tmp_path / "no-static",  # ensure mount is skipped
    )


@pytest.fixture
async def client(app):
    """AsyncClient with explicit lifespan context for background tasks."""
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            yield c


async def test_app_responds(client):
    r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_state_websocket_pushes_updates(app, tmp_path):
    """Use sync TestClient for websocket test since httpx doesn't support WS."""
    project = tmp_path / "wproj"; project.mkdir()
    # Use TestClient's context manager to ensure lifespan runs
    with TestClient(app) as sync_client:
        with sync_client.websocket_connect("/ws/state") as ws:
            r = sync_client.post(
                "/sessions",
                json={"project_path": str(project), "name": "wproj-001"},
            )
            assert r.status_code == 201
            sid = r.json()["id"]
            try:
                deadline = time.time() + 5
                received = None
                while time.time() < deadline:
                    try:
                        msg = ws.receive_json(mode="text")
                        if msg.get("session_id") == sid:
                            received = msg
                            break
                    except Exception:
                        pass
                assert received is not None
                assert received["state"] in {"bootstrapping", "waiting", "working"}
            finally:
                sync_client.delete(f"/sessions/{sid}")


async def test_reaper_marks_killed_processes_dead(client, app, tmp_path):
    """If a claude process exits externally, the reaper transitions
    the session to DEAD within ~2 reaper intervals."""
    import os
    import signal

    project = tmp_path / "rproj"
    project.mkdir()
    r = await client.post(
        "/sessions",
        json={"project_path": str(project), "name": "rproj-001"},
    )
    sid = r.json()["id"]
    sess = app.state.manager.get(sid)
    assert sess is not None and sess.pid is not None

    os.killpg(os.getpgid(sess.pid), signal.SIGKILL)

    deadline = asyncio.get_event_loop().time() + 5
    while asyncio.get_event_loop().time() < deadline:
        if sess.state.value == "dead":
            break
        await asyncio.sleep(0.1)
    assert sess.state.value == "dead"
    assert sess.pid is None
    assert sess.pty_master_fd is None


async def test_jsonl_discovery_binds_within_5s(client, tmp_path):
    project = tmp_path / "dproj"; project.mkdir()
    r = await client.post(
        "/sessions",
        json={"project_path": str(project), "name": "dproj-001"},
    )
    sid = r.json()["id"]
    try:
        encoded = str(project).replace("/", "-")
        target_dir = tmp_path / "claude_projects" / encoded
        deadline = asyncio.get_event_loop().time() + 5
        while asyncio.get_event_loop().time() < deadline:
            files = list(target_dir.glob("*.jsonl")) if target_dir.exists() else []
            if files:
                break
            await asyncio.sleep(0.1)
        assert list(target_dir.glob("*.jsonl"))

        deadline = asyncio.get_event_loop().time() + 3
        last_state = None
        while asyncio.get_event_loop().time() < deadline:
            r2 = await client.get("/sessions")
            if r2.json()["sessions"]:
                last_state = r2.json()["sessions"][0]["state"]
                if last_state != "bootstrapping":
                    break
            await asyncio.sleep(0.1)
        assert last_state in {"waiting", "working"}
    finally:
        await client.delete(f"/sessions/{sid}")


async def test_jsonl_discovery_timeout_marks_dead(tmp_path, monkeypatch):
    """If the JSONL never appears, the session transitions to DEAD."""
    import hive.server as server_mod
    from fastapi import FastAPI
    from httpx import AsyncClient, ASGITransport

    # Monkey-patch the timeout to 1s so the test runs quickly
    monkeypatch.setattr(server_mod, "JSONL_DISCOVERY_TIMEOUT", 1.0)
    monkeypatch.setattr(server_mod, "JSONL_DISCOVERY_POLL", 0.1)

    # Create a fake claude that just sleeps, doesn't write JSONL
    fake_no_jsonl = tmp_path / "fake_no_jsonl.py"
    fake_no_jsonl.write_text(
        "import signal, sys, time\n"
        "signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))\n"
        "while True: time.sleep(60)\n"
    )

    # Build app with the non-writing claude
    app = build_app(
        claude_command=[sys.executable, str(fake_no_jsonl)],
        claude_projects_dir=tmp_path / "claude_projects",
        state_path=tmp_path / "state.json",
        static_dir=tmp_path / "no-static",
    )

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            project = tmp_path / "deadproj"
            project.mkdir()
            r = await client.post(
                "/sessions",
                json={"project_path": str(project), "name": "deadproj-001"},
            )
            sid = r.json()["id"]

            # Wait up to 4s for DEAD transition (timeout is 1s + grace)
            sess = app.state.manager.get(sid)
            deadline = asyncio.get_event_loop().time() + 4
            while asyncio.get_event_loop().time() < deadline:
                if sess.state.value == "dead":
                    break
                await asyncio.sleep(0.1)
            assert sess.state.value == "dead", f"expected dead, got {sess.state.value}"
