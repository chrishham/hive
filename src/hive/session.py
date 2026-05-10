from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path


class SessionState(Enum):
    BOOTSTRAPPING = "bootstrapping"
    WORKING = "working"
    WAITING = "waiting"
    DEAD = "dead"


@dataclass
class Session:
    id: str
    name: str
    project_path: Path
    created_at: datetime
    pid: int | None = None
    pty_master_fd: int | None = None
    jsonl_path: Path | None = None
    claude_session_id: str | None = None
    state: SessionState = SessionState.BOOTSTRAPPING

    def to_persisted(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "project_path": str(self.project_path),
            "claude_session_id": self.claude_session_id,
        }
