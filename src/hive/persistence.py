from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from hive.session import Session


class StatePersistence:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> list[dict]:
        try:
            with self.path.open() as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return []
        sessions = data.get("sessions")
        if not isinstance(sessions, list):
            return []
        return sessions

    def save(self, sessions: list[Session]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"sessions": [s.to_persisted() for s in sessions]}
        # Atomic write: write to temp file in same dir, then rename.
        fd, tmp_name = tempfile.mkstemp(
            dir=self.path.parent, prefix=self.path.name + ".tmp."
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp_name, self.path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
