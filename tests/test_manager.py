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
