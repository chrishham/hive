import json

import pytest

from hive.install_hooks import HOOK_COMMAND, HOOK_EVENTS, install_hooks, uninstall_hooks


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


def test_hook_command_is_resilient():
    assert "|| true" in HOOK_COMMAND
    assert "hive-hook" in HOOK_COMMAND


def test_install_removes_legacy_hooks(fake_home):
    _settings_path(fake_home).parent.mkdir(parents=True, exist_ok=True)
    _settings_path(fake_home).write_text(json.dumps({
        "hooks": {
            "Stop": [
                {"matcher": "", "hooks": [{"type": "command", "command": "hive-hook"}]},
                {"matcher": "", "hooks": [{"type": "command", "command": "/old/venv/bin/python3 -m hive.hook_writer"}]},
            ]
        }
    }))
    install_hooks()
    data = json.loads(_settings_path(fake_home).read_text())
    all_commands = [
        h["command"]
        for entry in data["hooks"]["Stop"]
        for h in entry.get("hooks", [])
    ]
    assert HOOK_COMMAND in all_commands
    assert not any(cmd == "hive-hook" for cmd in all_commands)
    assert not any(cmd.endswith("-m hive.hook_writer") for cmd in all_commands)


def test_install_atomic_no_temp_left(tmp_path, monkeypatch):
    from hive.install_hooks import install_hooks
    monkeypatch.setattr("hive.install_hooks.settings_path", lambda: tmp_path / "settings.json")
    install_hooks()
    leftovers = [
        p.name for p in tmp_path.iterdir()
        if p.name not in {"settings.json", ".hive-settings.lock"}
    ]
    assert leftovers == []


def test_install_through_symlinked_settings(fake_home):
    real_dir = fake_home / "dotfiles" / "claude"
    real_dir.mkdir(parents=True)
    real = real_dir / "settings.json"
    real.write_text(json.dumps({"model": "opus"}))
    link = _settings_path(fake_home)
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(real)

    assert install_hooks() is True

    assert link.is_symlink()
    data = json.loads(real.read_text())
    assert data["model"] == "opus"
    for event in HOOK_EVENTS:
        commands = [
            h["command"]
            for entry in data["hooks"][event]
            for h in entry.get("hooks", [])
        ]
        assert HOOK_COMMAND in commands


def test_install_tolerates_malformed_hooks_key(tmp_path, monkeypatch):
    from hive.install_hooks import install_hooks
    target = tmp_path / "settings.json"
    target.write_text(json.dumps({"hooks": "not-a-dict", "other": {"k": 1}}))
    monkeypatch.setattr("hive.install_hooks.settings_path", lambda: target)
    assert install_hooks() is True
    data = json.loads(target.read_text())
    assert isinstance(data["hooks"], dict)
    assert data["other"] == {"k": 1}


# --- uninstall_hooks tests ---


def test_uninstall_removes_hive_hooks(fake_home):
    install_hooks()
    assert uninstall_hooks() is True
    data = json.loads(_settings_path(fake_home).read_text())
    assert "hooks" not in data


def test_uninstall_preserves_unrelated_hooks(fake_home):
    _settings_path(fake_home).parent.mkdir(parents=True, exist_ok=True)
    _settings_path(fake_home).write_text(json.dumps({
        "model": "opus",
        "hooks": {
            "Stop": [
                {"hooks": [{"type": "command", "command": "notify-send done"}]},
                {"matcher": "", "hooks": [{"type": "command", "command": HOOK_COMMAND}]},
            ],
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "audit.sh"}]}
            ],
        }
    }))
    assert uninstall_hooks() is True
    data = json.loads(_settings_path(fake_home).read_text())
    assert data["model"] == "opus"
    assert data["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "audit.sh"
    stop_cmds = [h["command"] for e in data["hooks"]["Stop"] for h in e.get("hooks", [])]
    assert "notify-send done" in stop_cmds
    assert HOOK_COMMAND not in stop_cmds


def test_uninstall_on_missing_file(fake_home):
    assert not _settings_path(fake_home).exists()
    assert uninstall_hooks() is True
    assert not _settings_path(fake_home).exists()


def test_uninstall_idempotent(fake_home):
    install_hooks()
    assert uninstall_hooks() is True
    assert uninstall_hooks() is True


def test_uninstall_removes_legacy_hooks(fake_home):
    _settings_path(fake_home).parent.mkdir(parents=True, exist_ok=True)
    _settings_path(fake_home).write_text(json.dumps({
        "hooks": {
            "Stop": [
                {"matcher": "", "hooks": [{"type": "command", "command": "hive-hook"}]},
                {"matcher": "", "hooks": [{"type": "command", "command": "/old/venv/python3 -m hive.hook_writer"}]},
            ]
        }
    }))
    assert uninstall_hooks() is True
    data = json.loads(_settings_path(fake_home).read_text())
    assert "hooks" not in data


def test_uninstall_cleans_empty_hooks_dict(fake_home):
    install_hooks()
    uninstall_hooks()
    data = json.loads(_settings_path(fake_home).read_text())
    assert "hooks" not in data


def test_uninstall_refuses_corrupt_settings(fake_home):
    _settings_path(fake_home).parent.mkdir(parents=True, exist_ok=True)
    _settings_path(fake_home).write_text("not json{")
    assert uninstall_hooks() is False
    assert _settings_path(fake_home).read_text() == "not json{"


def test_uninstall_through_symlinked_settings(fake_home):
    real_dir = fake_home / "dotfiles" / "claude"
    real_dir.mkdir(parents=True)
    real = real_dir / "settings.json"
    real.write_text(json.dumps({"model": "opus"}))
    link = _settings_path(fake_home)
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(real)

    install_hooks()
    assert uninstall_hooks() is True

    assert link.is_symlink()
    data = json.loads(real.read_text())
    assert data["model"] == "opus"
    assert "hooks" not in data
