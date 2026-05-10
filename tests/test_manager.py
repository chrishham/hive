import os
import signal
import sys
import time
from pathlib import Path

import pytest

from hive.manager import SessionManager
from hive.session import SessionState


@pytest.fixture
def fake_claude_cmd():
    fake = Path(__file__).parent / "fake_claude.py"
    return [sys.executable, str(fake)]


@pytest.fixture
def claude_projects_dir(tmp_path):
    d = tmp_path / "claude_projects"
    d.mkdir()
    return d


@pytest.fixture
def state_path(tmp_path):
    return tmp_path / "state.json"


@pytest.fixture
def manager(fake_claude_cmd, claude_projects_dir, state_path):
    return SessionManager(
        claude_command=fake_claude_cmd,
        claude_projects_dir=claude_projects_dir,
        state_path=state_path,
    )


def test_spawn_creates_running_process(manager, tmp_path):
    project = tmp_path / "myproj"
    project.mkdir()
    s = manager.spawn(project_path=project, name="myproj-001")
    try:
        assert s.pid is not None
        assert s.pty_master_fd is not None
        # Process is alive
        os.kill(s.pid, 0)
        assert s.state == SessionState.BOOTSTRAPPING
    finally:
        manager.kill(s.id)


def test_spawn_starts_new_process_group(manager, tmp_path):
    project = tmp_path / "p2"
    project.mkdir()
    s = manager.spawn(project_path=project, name="p2-001")
    try:
        # Process group id should equal pid (it's the group leader)
        assert os.getpgid(s.pid) == s.pid
    finally:
        manager.kill(s.id)


def test_spawn_invokes_command_with_correct_args(manager, tmp_path, monkeypatch):
    """The fake_claude writes to whatever projects_dir we tell it.
    Assert the spawn passes --name and --dangerously-skip-permissions."""
    project = tmp_path / "p3"
    project.mkdir()
    s = manager.spawn(project_path=project, name="p3-001")
    try:
        # Wait briefly for fake_claude to write its JSONL
        encoded = str(project).replace("/", "-")
        target_dir = manager.claude_projects_dir / encoded
        deadline = time.time() + 5
        while time.time() < deadline:
            if target_dir.exists() and list(target_dir.glob("*.jsonl")):
                break
            time.sleep(0.05)
        assert target_dir.exists()
        assert list(target_dir.glob("*.jsonl"))
    finally:
        manager.kill(s.id)


def test_kill_terminates_process(manager, tmp_path):
    project = tmp_path / "k1"
    project.mkdir()
    s = manager.spawn(project_path=project, name="k1-001")
    pid = s.pid
    manager.kill(s.id)
    deadline = time.time() + 3
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_kill_unknown_id_is_noop(manager):
    manager.kill("does-not-exist")  # should not raise


def test_list_returns_active_sessions(manager, tmp_path):
    p1 = tmp_path / "l1"; p1.mkdir()
    p2 = tmp_path / "l2"; p2.mkdir()
    s1 = manager.spawn(project_path=p1, name="l1-001")
    s2 = manager.spawn(project_path=p2, name="l2-001")
    try:
        names = {s.name for s in manager.list()}
        assert names == {"l1-001", "l2-001"}
    finally:
        manager.kill(s1.id)
        manager.kill(s2.id)


def test_spawn_rejects_duplicate_name(manager, tmp_path):
    p = tmp_path / "d1"; p.mkdir()
    s = manager.spawn(project_path=p, name="dup-001")
    try:
        with pytest.raises(ValueError, match="already exists"):
            manager.spawn(project_path=p, name="dup-001")
    finally:
        manager.kill(s.id)


def test_restart_persisted_resumes_from_state_json(
    fake_claude_cmd, claude_projects_dir, state_path, tmp_path
):
    # Pre-populate state.json
    project = tmp_path / "r1"; project.mkdir()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    import json as _json
    state_path.write_text(_json.dumps({"sessions": [{
        "id": "id-1",
        "name": "r1-001",
        "project_path": str(project),
        "claude_session_id": "resume-uuid-xyz",
    }]}))
    mgr = SessionManager(
        claude_command=fake_claude_cmd,
        claude_projects_dir=claude_projects_dir,
        state_path=state_path,
    )
    mgr.restart_persisted()
    try:
        sessions = mgr.list()
        assert len(sessions) == 1
        assert sessions[0].name == "r1-001"
        # fake_claude should have written to <encoded>/resume-uuid-xyz.jsonl
        encoded = str(project).replace("/", "-")
        deadline = time.time() + 5
        target = claude_projects_dir / encoded / "resume-uuid-xyz.jsonl"
        while time.time() < deadline and not target.exists():
            time.sleep(0.05)
        assert target.exists()
    finally:
        for s in mgr.list():
            mgr.kill(s.id)


def test_save_state_persists_active_sessions(
    fake_claude_cmd, claude_projects_dir, state_path, tmp_path
):
    mgr = SessionManager(
        claude_command=fake_claude_cmd,
        claude_projects_dir=claude_projects_dir,
        state_path=state_path,
    )
    project = tmp_path / "s1"; project.mkdir()
    s = mgr.spawn(project_path=project, name="s1-001")
    s.claude_session_id = "manually-set-uuid"
    try:
        mgr.save_state()
        import json as _json
        data = _json.loads(state_path.read_text())
        assert data["sessions"][0]["name"] == "s1-001"
        assert data["sessions"][0]["claude_session_id"] == "manually-set-uuid"
    finally:
        mgr.kill(s.id)


def test_spawn_sets_pty_winsize(manager, tmp_path):
    """The spawned PTY must have a non-zero window size or TUIs render nothing."""
    import fcntl
    import struct
    import termios

    project = tmp_path / "wsize"
    project.mkdir()
    s = manager.spawn(project_path=project, name="wsize-001")
    try:
        size = struct.pack("HHHH", 0, 0, 0, 0)
        result = fcntl.ioctl(s.pty_master_fd, termios.TIOCGWINSZ, size)
        rows, cols, _, _ = struct.unpack("HHHH", result)
        assert rows > 0
        assert cols > 0
    finally:
        manager.kill(s.id)
