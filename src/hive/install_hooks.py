from __future__ import annotations

import fcntl
import json
import os
import shlex
import sys
from pathlib import Path

from hive.safety import atomic_write_text

HOOK_COMMAND = f"{shlex.quote(sys.executable)} -m hive.hook_writer"
HOOK_EVENTS = (
    "UserPromptSubmit",
    "Stop",
    "SubagentStop",
    "Notification",
    "SessionStart",
    "SessionEnd",
)


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

            atomic_write_text(path, json.dumps(settings, indent=2))
            return True
        finally:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)
    except OSError:
        return False
