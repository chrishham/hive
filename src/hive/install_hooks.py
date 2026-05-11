from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path

from hive.safety import atomic_write_text

HOOK_COMMAND = "command -v hive-hook >/dev/null 2>&1 && hive-hook || true"
_LEGACY_COMMANDS = {"hive-hook", "-m hive.hook_writer"}
HOOK_EVENTS = (
    "UserPromptSubmit",
    "Stop",
    "SubagentStop",
    "Notification",
    "SessionStart",
    "SessionEnd",
)


def _is_hive_entry(entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    for h in entry.get("hooks", []):
        if not isinstance(h, dict):
            continue
        cmd = h.get("command")
        if not isinstance(cmd, str):
            continue
        if cmd == HOOK_COMMAND or any(cmd.endswith(s) for s in _LEGACY_COMMANDS):
            return True
    return False


def settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def hook_installed(settings: dict | None = None) -> bool:
    if settings is None:
        try:
            settings = json.loads(settings_path().read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return False
    if not isinstance(settings, dict):
        return False
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return False
    for entries in hooks.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            for h in entry.get("hooks", []) if isinstance(entry, dict) else []:
                if isinstance(h, dict) and h.get("command") == HOOK_COMMAND:
                    return True
    return False


def install_hooks() -> bool:
    path = settings_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.parent / ".hive-settings.lock"
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            if path.exists():
                try:
                    settings = json.loads(path.read_text())
                except json.JSONDecodeError:
                    return False
                if not isinstance(settings, dict):
                    return False
            else:
                settings = {}

            existing_hooks = settings.get("hooks")
            if not isinstance(existing_hooks, dict):
                existing_hooks = {}
                settings["hooks"] = existing_hooks

            for event in HOOK_EVENTS:
                entries = existing_hooks.get(event)
                if not isinstance(entries, list):
                    entries = []
                    existing_hooks[event] = entries

                existing_hooks[event] = [
                    entry for entry in entries
                    if not _is_hive_entry(entry)
                ]
                entries = existing_hooks[event]

                already = any(
                    isinstance(h, dict) and h.get("command") == HOOK_COMMAND
                    for entry in entries if isinstance(entry, dict)
                    for h in entry.get("hooks", [])
                )
                if not already:
                    entries.append({
                        "matcher": "",
                        "hooks": [{"type": "command", "command": HOOK_COMMAND}],
                    })

            atomic_write_text(path, json.dumps(settings, indent=2), follow_symlinks=True)
            return True
        finally:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)
    except OSError:
        return False


def uninstall_hooks() -> bool:
    path = settings_path()
    if not path.exists():
        return True
    try:
        lock_path = path.parent / ".hive-settings.lock"
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            if not path.exists():
                return True
            try:
                settings = json.loads(path.read_text())
            except json.JSONDecodeError:
                return False
            if not isinstance(settings, dict):
                return True

            hooks = settings.get("hooks")
            if not isinstance(hooks, dict):
                return True

            for event in list(hooks.keys()):
                entries = hooks[event]
                if not isinstance(entries, list):
                    continue
                hooks[event] = [e for e in entries if not _is_hive_entry(e)]
                if not hooks[event]:
                    del hooks[event]

            if not hooks:
                del settings["hooks"]

            atomic_write_text(path, json.dumps(settings, indent=2), follow_symlinks=True)
            return True
        finally:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)
    except OSError:
        return False
