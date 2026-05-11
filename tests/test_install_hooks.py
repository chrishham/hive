import json
import shlex
import sys

import pytest

from hive.install_hooks import HOOK_COMMAND, HOOK_EVENTS, install_hooks


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def _settings_path(home):
    return home / ".claude" / "settings.json"


def test_install_into_empty(fake_home):
    assert install_hooks() is True
    data = json.loads(_settings_path(fake_home).read_text())
    for event in HOOK_EVENTS:
        entries = data["hooks"][event]
        assert any(
            h.get("command") == HOOK_COMMAND
            for entry in entries
            for h in entry.get("hooks", [])
        )


def test_install_preserves_existing_unrelated(fake_home):
    _settings_path(fake_home).parent.mkdir(parents=True, exist_ok=True)
    _settings_path(fake_home).write_text(json.dumps({
        "model": "opus",
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "audit.sh"}]}
            ]
        }
    }))
    install_hooks()
    data = json.loads(_settings_path(fake_home).read_text())
    assert data["model"] == "opus"
    assert data["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "audit.sh"


def test_install_is_idempotent(fake_home):
    install_hooks()
    install_hooks()
    data = json.loads(_settings_path(fake_home).read_text())
    for event in HOOK_EVENTS:
        commands = [
            h["command"]
            for entry in data["hooks"][event]
            for h in entry.get("hooks", [])
            if h.get("command") == HOOK_COMMAND
        ]
        assert len(commands) == 1


def test_install_refuses_corrupt_settings(fake_home):
    _settings_path(fake_home).parent.mkdir(parents=True, exist_ok=True)
    _settings_path(fake_home).write_text("not json{")
    assert install_hooks() is False
    assert _settings_path(fake_home).read_text() == "not json{"


def test_install_returns_false_on_write_error(fake_home, monkeypatch):
    def raise_oserror(*args, **kwargs):
        raise OSError("simulated permission denied")
    monkeypatch.setattr("hive.install_hooks.atomic_write_text", raise_oserror)
    assert install_hooks() is False


def test_install_writes_absolute_command(tmp_path, monkeypatch):
    from hive.install_hooks import install_hooks, HOOK_COMMAND, settings_path
    monkeypatch.setattr("hive.install_hooks.settings_path", lambda: tmp_path / "settings.json")
    assert install_hooks() is True
    data = json.loads((tmp_path / "settings.json").read_text())
    expected = f"{shlex.quote(sys.executable)} -m hive.hook_writer"
    found_commands = [
        h["command"]
        for entries in data["hooks"].values()
        for entry in entries
        for h in entry.get("hooks", [])
    ]
    assert expected in found_commands
    assert HOOK_COMMAND == expected


def test_install_atomic_no_temp_left(tmp_path, monkeypatch):
    from hive.install_hooks import install_hooks
    monkeypatch.setattr("hive.install_hooks.settings_path", lambda: tmp_path / "settings.json")
    install_hooks()
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "settings.json"]
    assert leftovers == []


def test_install_tolerates_malformed_hooks_key(tmp_path, monkeypatch):
    from hive.install_hooks import install_hooks
    target = tmp_path / "settings.json"
    target.write_text(json.dumps({"hooks": "not-a-dict", "other": {"k": 1}}))
    monkeypatch.setattr("hive.install_hooks.settings_path", lambda: target)
    assert install_hooks() is True
    data = json.loads(target.read_text())
    assert isinstance(data["hooks"], dict)
    assert data["other"] == {"k": 1}
