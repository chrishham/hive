import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hive.manager import SessionManager
from hive.routes import build_router
from hive.watcher import StateWatcher


@pytest.fixture
def fake_claude_cmd():
    fake = Path(__file__).parent / "fake_claude.py"
    return [sys.executable, str(fake)]


@pytest.fixture
def manager(fake_claude_cmd, tmp_path):
    return SessionManager(
        claude_command=fake_claude_cmd,
        claude_projects_dir=tmp_path / "claude_projects",
        state_path=tmp_path / "state.json",
    )


@pytest.fixture
def watcher():
    return StateWatcher(poll_interval=0.05)


@pytest.fixture
def client(manager, watcher):
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(build_router(manager, watcher))
    return TestClient(app)


def test_list_empty(client):
    r = client.get("/sessions")
    assert r.status_code == 200
    assert r.json() == {"sessions": []}


def test_create_session(client, manager, tmp_path):
    project = tmp_path / "rp1"; project.mkdir()
    r = client.post("/sessions", json={
        "project_path": str(project),
        "name": "rp1-001",
    })
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "rp1-001"
    assert body["state"] == "bootstrapping"
    assert "id" in body
    # cleanup
    manager.kill(body["id"])


def test_create_duplicate_returns_409(client, manager, tmp_path):
    project = tmp_path / "rp2"; project.mkdir()
    r1 = client.post("/sessions", json={
        "project_path": str(project), "name": "dup"})
    assert r1.status_code == 201
    r2 = client.post("/sessions", json={
        "project_path": str(project), "name": "dup"})
    assert r2.status_code == 409
    manager.kill(r1.json()["id"])


def test_create_missing_project_returns_400(client):
    r = client.post("/sessions", json={
        "project_path": "/nonexistent/path/that/does/not/exist",
        "name": "bad",
    })
    assert r.status_code == 400


def test_delete_session(client, manager, tmp_path):
    project = tmp_path / "rp3"; project.mkdir()
    r = client.post("/sessions", json={
        "project_path": str(project), "name": "rp3-001"})
    sid = r.json()["id"]
    r2 = client.delete(f"/sessions/{sid}")
    assert r2.status_code == 204
    assert client.get("/sessions").json()["sessions"] == []


def test_delete_unknown_returns_404(client):
    r = client.delete("/sessions/does-not-exist")
    assert r.status_code == 404


def test_create_with_missing_claude_binary_returns_400(tmp_path):
    """If the claude binary doesn't exist, POST /sessions returns 400."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from hive.manager import SessionManager
    from hive.routes import build_router
    from hive.watcher import StateWatcher

    mgr = SessionManager(
        claude_command=["/nonexistent/path/to/claude-binary"],
        claude_projects_dir=tmp_path / "claude_projects",
        state_path=tmp_path / "state.json",
    )
    app = FastAPI()
    app.include_router(build_router(mgr, StateWatcher(poll_interval=0.05)))
    client = TestClient(app)

    project = tmp_path / "p"
    project.mkdir()
    r = client.post(
        "/sessions",
        json={"project_path": str(project), "name": "p-001"},
    )
    assert r.status_code == 400
    assert "claude binary not found" in r.json()["detail"].lower()
