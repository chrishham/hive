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

from hive.safety import atomic_write_text, sanitize_session_name

DEFAULT_SCAN_PATHS = [str(Path.home() / "Projects")]
DEFAULT_CLONE_PATH = str(Path.home() / "Projects")
CONFIG_DIR = Path.home() / ".config" / "hive"


@dataclass
class HiveConfig:
    scan_paths: list[str] = field(default_factory=lambda: list(DEFAULT_SCAN_PATHS))
    clone_path: str = DEFAULT_CLONE_PATH
    refresh_interval_ms: int = 5000
    preview_lines: int = 20
    tmux_session_name: str = "hive"

    @classmethod
    def defaults(cls) -> HiveConfig:
        return cls()

    @classmethod
    def load(cls, path: Path) -> HiveConfig:
        if not path.exists():
            return cls.defaults()
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
            if not isinstance(data, dict):
                return cls.defaults()
            projects = data.get("projects", {}) if isinstance(data.get("projects"), dict) else {}
            display = data.get("display", {}) if isinstance(data.get("display"), dict) else {}
            tmux = data.get("tmux", {}) if isinstance(data.get("tmux"), dict) else {}
            return cls(
                scan_paths=projects.get("scan_paths", DEFAULT_SCAN_PATHS),
                clone_path=projects.get("clone_path", DEFAULT_CLONE_PATH),
                refresh_interval_ms=int(display.get("refresh_interval_ms", 2500)),
                preview_lines=int(display.get("preview_lines", 20)),
                tmux_session_name=sanitize_session_name(str(tmux.get("session_name", "hive"))),
            )
        except (tomllib.TOMLDecodeError, OSError, TypeError, ValueError) as exc:
            print(f"hive: config load failed, using defaults: {exc}", file=sys.stderr)
            return cls.defaults()

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
        try:
            with open(path) as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return cls()
            sessions = data.get("sessions", {}) if isinstance(data.get("sessions"), dict) else {}
            projects = data.get("projects", {}) if isinstance(data.get("projects"), dict) else {}
            return cls(sessions=sessions, projects=projects)
        except (json.JSONDecodeError, OSError, TypeError, ValueError) as exc:
            print(f"hive: state load failed, using empty: {exc}", file=sys.stderr)
            return cls()

    @classmethod
    def load_default(cls) -> HiveState:
        return cls.load(CONFIG_DIR / "state.json")

    def save(self, path: Path) -> None:
        atomic_write_text(path, json.dumps({"sessions": self.sessions, "projects": self.projects}, indent=2))

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
