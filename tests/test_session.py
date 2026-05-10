from datetime import datetime
from pathlib import Path

from hive.session import Session, SessionState


def test_state_values():
    assert {s.value for s in SessionState} == {
        "bootstrapping", "working", "waiting", "dead"
    }


def test_session_defaults():
    s = Session(
        id="abc",
        name="my-project-001",
        project_path=Path("/tmp/proj"),
        created_at=datetime(2026, 5, 10),
    )
    assert s.pid is None
    assert s.pty_master_fd is None
    assert s.jsonl_path is None
    assert s.claude_session_id is None
    assert s.state == SessionState.BOOTSTRAPPING


def test_session_to_persisted_dict():
    s = Session(
        id="abc",
        name="my-project-001",
        project_path=Path("/tmp/proj"),
        created_at=datetime(2026, 5, 10),
        claude_session_id="ee8a-uuid",
    )
    d = s.to_persisted()
    assert d == {
        "id": "abc",
        "name": "my-project-001",
        "project_path": "/tmp/proj",
        "claude_session_id": "ee8a-uuid",
    }
