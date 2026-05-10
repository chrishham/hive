from __future__ import annotations

import os
import pty
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from hive.session import Session, SessionState


class SessionManager:
    def __init__(
        self,
        claude_command: list[str] | None = None,
        claude_projects_dir: Path | None = None,
        state_path: Path | None = None,
    ):
        self.claude_command = claude_command or ["claude"]
        self.claude_projects_dir = (
            claude_projects_dir
            or Path.home() / ".claude" / "projects"
        )
        self.state_path = (
            state_path
            or Path.home() / ".config" / "hive" / "state.json"
        )
        self._sessions: dict[str, Session] = {}

    def spawn(self, project_path: Path, name: str) -> Session:
        if any(s.name == name for s in self._sessions.values()):
            raise ValueError(f"session name '{name}' already exists")

        master_fd, slave_fd = pty.openpty()
        env = {
            **os.environ,
            "TERM": "xterm-256color",
        }
        cmd = list(self.claude_command) + [
            "--dangerously-skip-permissions",
            "--name", name,
        ]
        # Only pass --projects-dir to the spawned binary if we're overriding
        # the default. Real `claude` doesn't accept this flag; fake_claude
        # (used in tests) does.
        default_dir = Path.home() / ".claude" / "projects"
        if self.claude_projects_dir != default_dir:
            cmd += ["--projects-dir", str(self.claude_projects_dir)]
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(project_path),
                env=env,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                start_new_session=True,
                close_fds=True,
            )
        finally:
            os.close(slave_fd)

        session = Session(
            id=str(uuid.uuid4()),
            name=name,
            project_path=project_path,
            created_at=datetime.now(timezone.utc),
            pid=proc.pid,
            pty_master_fd=master_fd,
            state=SessionState.BOOTSTRAPPING,
        )
        self._sessions[session.id] = session
        return session

    def kill(self, session_id: str) -> None:
        s = self._sessions.pop(session_id, None)
        if s is None or s.pid is None:
            return
        try:
            os.killpg(os.getpgid(s.pid), 15)  # SIGTERM
        except ProcessLookupError:
            pass
        if s.pty_master_fd is not None:
            try:
                os.close(s.pty_master_fd)
            except OSError:
                pass
