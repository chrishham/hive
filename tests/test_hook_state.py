import json
import pytest

from hive.detector import SessionState
from hive.hook_state import read_session_state, read_session_id, remove_session_state, state_file_path


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def _write_state(home, name, payload):
    path = home / ".claude" / "hive" / "state" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    return path


def test_missing_file_returns_none(fake_home):
    assert read_session_state("nope") is None


def test_reads_working(fake_home):
    _write_state(fake_home, "foo", {"state": "working"})
    assert read_session_state("foo") == SessionState.WORKING


def test_reads_waiting(fake_home):
    _write_state(fake_home, "foo", {"state": "waiting"})
    assert read_session_state("foo") == SessionState.WAITING


def test_corrupt_json_returns_none(fake_home):
    path = fake_home / ".claude" / "hive" / "state" / "foo.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json{")
    assert read_session_state("foo") is None


def test_unknown_state_returns_none(fake_home):
    _write_state(fake_home, "foo", {"state": "exploding"})
    assert read_session_state("foo") is None


def test_remove_session_state_idempotent(fake_home):
    _write_state(fake_home, "foo", {"state": "waiting"})
    remove_session_state("foo")
    remove_session_state("foo")  # second call must not raise
    assert read_session_state("foo") is None


def test_state_file_path_uses_home(fake_home):
    assert state_file_path("bar") == fake_home / ".claude" / "hive" / "state" / "bar.json"


@pytest.mark.parametrize("bad", ["../escape", "a/b", "a\\b", "..", ".", ""])
def test_state_file_path_rejects_unsafe_names(fake_home, bad):
    with pytest.raises(ValueError):
        state_file_path(bad)


def test_read_session_state_unsafe_name_returns_none(fake_home):
    assert read_session_state("../escape") is None


def test_remove_session_state_unsafe_name_no_raise(fake_home):
    remove_session_state("../escape")


def test_read_session_id_returns_id(fake_home):
    _write_state(fake_home, "foo", {"state": "working", "session_id": "abc-123"})
    assert read_session_id("foo") == "abc-123"


def test_read_session_id_missing_file_returns_none(fake_home):
    assert read_session_id("nope") is None


def test_read_session_id_no_session_id_field(fake_home):
    _write_state(fake_home, "foo", {"state": "working"})
    assert read_session_id("foo") is None


def test_read_session_id_unsafe_name_returns_none(fake_home):
    assert read_session_id("../escape") is None
