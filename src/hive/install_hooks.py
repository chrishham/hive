from __future__ import annotations

import json
from pathlib import Path

HOOK_COMMAND = "hive-hook"
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


def install_hooks() -> bool:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        try:
            settings = json.loads(path.read_text())
        except json.JSONDecodeError:
            return False
        if not isinstance(settings, dict):
            return False
    else:
        settings = {}

    hooks = settings.setdefault("hooks", {})
    for event in HOOK_EVENTS:
        entries = hooks.setdefault(event, [])
        already = any(
            h.get("command") == HOOK_COMMAND
            for entry in entries
            for h in entry.get("hooks", [])
        )
        if not already:
            entries.append({
                "matcher": "",
                "hooks": [{"type": "command", "command": HOOK_COMMAND}],
            })

    path.write_text(json.dumps(settings, indent=2))
    return True
