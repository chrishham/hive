# tests/test_hook_writer.py
import json
import subprocess
import sys

import pytest


def _run(payload: dict, env: dict, cwd):
    return subprocess.run(
        [sys.executable, "-m", "hive.hook_writer"],
        input=json.dumps(payload).encode(),
        env=env,
        cwd=cwd,
        capture_output=True,
        timeout=5,
    )


@pytest.fixture
def hook_env(tmp_path, monkeypatch):
    return {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin", "PYTHONPATH": "src"}


def _state_file(home, name):
    from pathlib import Path
    return Path(home) / ".claude" / "hive" / "state" / f"{name}.json"


def test_no_hive_session_exits_zero(tmp_path, hook_env):
    env = {**hook_env}
    env.pop("HIVE_SESSION", None)
    result = _run({"hook_event_name": "Stop", "session_id": "x"}, env, "/data/Projects/hive")
    assert result.returncode == 0
    assert not _state_file(tmp_path, "anything").exists()


def test_user_prompt_submit_writes_working(tmp_path, hook_env):
    env = {**hook_env, "HIVE_SESSION": "foo"}
    result = _run(
        {"hook_event_name": "UserPromptSubmit", "session_id": "abc"},
        env,
        "/data/Projects/hive",
    )
    assert result.returncode == 0
    data = json.loads(_state_file(tmp_path, "foo").read_text())
    assert data["state"] == "working"
    assert data["session_id"] == "abc"
    assert data["event"] == "UserPromptSubmit"
    assert "updated_at" in data


def test_stop_writes_waiting(tmp_path, hook_env):
    env = {**hook_env, "HIVE_SESSION": "foo"}
    _run({"hook_event_name": "Stop", "session_id": "abc"}, env, "/data/Projects/hive")
    data = json.loads(_state_file(tmp_path, "foo").read_text())
    assert data["state"] == "waiting"


def test_session_end_deletes_file(tmp_path, hook_env):
    env = {**hook_env, "HIVE_SESSION": "foo"}
    _run({"hook_event_name": "Stop", "session_id": "abc"}, env, "/data/Projects/hive")
    assert _state_file(tmp_path, "foo").exists()
    _run({"hook_event_name": "SessionEnd", "session_id": "abc"}, env, "/data/Projects/hive")
    assert not _state_file(tmp_path, "foo").exists()


def test_invalid_json_exits_zero(tmp_path, hook_env):
    env = {**hook_env, "HIVE_SESSION": "foo"}
    result = subprocess.run(
        [sys.executable, "-m", "hive.hook_writer"],
        input=b"not json",
        env=env,
        cwd="/data/Projects/hive",
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == 0
    assert not _state_file(tmp_path, "foo").exists()


def test_unknown_event_is_noop(tmp_path, hook_env):
    env = {**hook_env, "HIVE_SESSION": "foo"}
    _run({"hook_event_name": "Mystery"}, env, "/data/Projects/hive")
    assert not _state_file(tmp_path, "foo").exists()
