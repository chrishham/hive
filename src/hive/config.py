from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 12):
    import tomllib
else:
    import tomli as tomllib

DEFAULT_SCAN_PATHS = [str(Path.home() / "Projects")]
DEFAULT_CLONE_PATH = str(Path.home() / "Projects")
CONFIG_DIR = Path.home() / ".config" / "hive"


@dataclass
class HiveConfig:
    scan_paths: list[str] = field(default_factory=lambda: list(DEFAULT_SCAN_PATHS))
    clone_path: str = DEFAULT_CLONE_PATH
    refresh_interval_ms: int = 2500
    preview_lines: int = 20
    idle_timeout_seconds: int = 300
    tmux_session_name: str = "hive"

    @classmethod
    def defaults(cls) -> HiveConfig:
        return cls()

    @classmethod
    def load(cls, path: Path) -> HiveConfig:
        if not path.exists():
            return cls.defaults()
        with open(path, "rb") as f:
            data = tomllib.load(f)
        projects = data.get("projects", {})
        display = data.get("display", {})
        tmux = data.get("tmux", {})
        return cls(
            scan_paths=projects.get("scan_paths", DEFAULT_SCAN_PATHS),
            clone_path=projects.get("clone_path", DEFAULT_CLONE_PATH),
            refresh_interval_ms=display.get("refresh_interval_ms", 2500),
            preview_lines=display.get("preview_lines", 20),
            idle_timeout_seconds=display.get("idle_timeout_seconds", 300),
            tmux_session_name=tmux.get("session_name", "hive"),
        )

    @classmethod
    def load_default(cls) -> HiveConfig:
        return cls.load(CONFIG_DIR / "config.toml")


@dataclass
class HiveState:
    sessions: dict[str, dict[str, Any]] = field(default_factory=dict)
    projects: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> HiveState:
        if not path.exists():
            return cls()
        with open(path) as f:
            data = json.load(f)
        return cls(
            sessions=data.get("sessions", {}),
            projects=data.get("projects", {}),
        )

    @classmethod
    def load_default(cls) -> HiveState:
        return cls.load(CONFIG_DIR / "state.json")

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump({"sessions": self.sessions, "projects": self.projects}, f, indent=2)

    def save_default(self) -> None:
        self.save(CONFIG_DIR / "state.json")

    def touch_project(self, project_path: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.projects[project_path] = {"last_used": now}

    def add_session(self, name: str, project_path: str, tmux_window: int, claude_session_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.sessions[name] = {
            "project_path": project_path,
            "created_at": now,
            "last_used": now,
            "tmux_window": tmux_window,
            "claude_session_id": claude_session_id,
        }
        self.touch_project(project_path)

    def remove_session(self, name: str) -> None:
        self.sessions.pop(name, None)
