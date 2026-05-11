# tests/test_hook_writer.py
import json
import subprocess
import sys
import threading
from io import StringIO

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
    assert result.stdout == b""
    assert result.stderr == b""
    assert not _state_file(tmp_path, "anything").exists()


def test_user_prompt_submit_writes_working(tmp_path, hook_env):
    env = {**hook_env, "HIVE_SESSION": "foo"}
    result = _run(
        {"hook_event_name": "UserPromptSubmit", "session_id": "abc"},
        env,
        "/data/Projects/hive",
    )
    assert result.returncode == 0
    assert result.stdout == b""
    assert result.stderr == b""
    data = json.loads(_state_file(tmp_path, "foo").read_text())
    assert data["state"] == "working"
    assert data["session_id"] == "abc"
    assert data["event"] == "UserPromptSubmit"
    assert "updated_at" in data


def test_stop_writes_waiting(tmp_path, hook_env):
    env = {**hook_env, "HIVE_SESSION": "foo"}
    result = _run({"hook_event_name": "Stop", "session_id": "abc"}, env, "/data/Projects/hive")
    assert result.returncode == 0
    assert result.stdout == b""
    assert result.stderr == b""
    data = json.loads(_state_file(tmp_path, "foo").read_text())
    assert data["state"] == "waiting"


def test_session_end_deletes_file(tmp_path, hook_env):
    env = {**hook_env, "HIVE_SESSION": "foo"}
    result1 = _run({"hook_event_name": "Stop", "session_id": "abc"}, env, "/data/Projects/hive")
    assert result1.returncode == 0
    assert result1.stdout == b""
    assert result1.stderr == b""
    assert _state_file(tmp_path, "foo").exists()
    result2 = _run({"hook_event_name": "SessionEnd", "session_id": "abc"}, env, "/data/Projects/hive")
    assert result2.returncode == 0
    assert result2.stdout == b""
    assert result2.stderr == b""
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
    assert result.stdout == b""
    assert result.stderr == b""
    assert not _state_file(tmp_path, "foo").exists()


def test_unknown_event_is_noop(tmp_path, hook_env):
    env = {**hook_env, "HIVE_SESSION": "foo"}
    result = _run({"hook_event_name": "Mystery"}, env, "/data/Projects/hive")
    assert result.returncode == 0
    assert result.stdout == b""
    assert result.stderr == b""
    assert not _state_file(tmp_path, "foo").exists()


def test_subagent_stop_writes_waiting(tmp_path, hook_env):
    env = {**hook_env, "HIVE_SESSION": "foo"}
    result = _run({"hook_event_name": "SubagentStop", "session_id": "abc"}, env, "/data/Projects/hive")
    assert result.returncode == 0
    assert result.stdout == b""
    assert result.stderr == b""
    data = json.loads(_state_file(tmp_path, "foo").read_text())
    assert data["state"] == "waiting"
    assert data["event"] == "SubagentStop"


def test_notification_writes_waiting(tmp_path, hook_env):
    env = {**hook_env, "HIVE_SESSION": "foo"}
    result = _run({"hook_event_name": "Notification", "session_id": "abc"}, env, "/data/Projects/hive")
    assert result.returncode == 0
    assert result.stdout == b""
    assert result.stderr == b""
    data = json.loads(_state_file(tmp_path, "foo").read_text())
    assert data["state"] == "waiting"
    assert data["event"] == "Notification"


def test_session_start_writes_waiting(tmp_path, hook_env):
    env = {**hook_env, "HIVE_SESSION": "foo"}
    result = _run({"hook_event_name": "SessionStart", "session_id": "abc"}, env, "/data/Projects/hive")
    assert result.returncode == 0
    assert result.stdout == b""
    assert result.stderr == b""
    data = json.loads(_state_file(tmp_path, "foo").read_text())
    assert data["state"] == "waiting"
    assert data["event"] == "SessionStart"


def test_oversized_stdin_is_rejected(tmp_path, hook_env):
    env = {**hook_env, "HIVE_SESSION": "foo"}
    huge = b"x" * (200 * 1024)  # 200KB > cap
    result = subprocess.run(
        [sys.executable, "-m", "hive.hook_writer"],
        input=huge,
        env=env,
        cwd="/data/Projects/hive",
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == 0
    assert not _state_file(tmp_path, "foo").exists()


def test_oversized_session_id_is_truncated_or_rejected(tmp_path, hook_env):
    env = {**hook_env, "HIVE_SESSION": "foo"}
    payload = {"hook_event_name": "Stop", "session_id": "a" * 10000}
    result = _run(payload, env, "/data/Projects/hive")
    assert result.returncode == 0
    sf = _state_file(tmp_path, "foo")
    if sf.exists():
        data = json.loads(sf.read_text())
        assert len(data["session_id"]) <= 256


def test_concurrent_writers_no_collision(tmp_path, monkeypatch):
    from hive import hook_writer

    monkeypatch.setattr(hook_writer, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(hook_writer, "state_file_path", lambda n: tmp_path / f"{n}.json")
    monkeypatch.setenv("HIVE_SESSION", "race-test")

    payloads = [
        {"hook_event_name": "Stop", "session_id": f"s{i}"}
        for i in range(20)
    ]
    errors: list[Exception] = []

    def fire(p):
        try:
            monkeypatch.setattr("sys.stdin", StringIO(json.dumps(p)))
            hook_writer.main()
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=fire, args=(p,)) for p in payloads]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    final = json.loads((tmp_path / "race-test.json").read_text())
    assert final["state"] == "waiting"
