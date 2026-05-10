from __future__ import annotations

import os
import pty
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from hive.persistence import StatePersistence
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
        import time
        s = self._sessions.pop(session_id, None)
        if s is None or s.pid is None:
            return
        # Try graceful SIGTERM first
        try:
            os.killpg(os.getpgid(s.pid), 15)  # SIGTERM
        except ProcessLookupError:
            pass
        else:
            # Wait up to 1 second for process to terminate
            deadline = time.time() + 1.0
            while time.time() < deadline:
                try:
                    os.kill(s.pid, 0)  # Check if still alive
                except ProcessLookupError:
                    break  # Process is gone
                time.sleep(0.05)
            # If still alive, force kill
            try:
                os.killpg(os.getpgid(s.pid), 9)  # SIGKILL
            except ProcessLookupError:
                pass
        # Reap zombie if any
        try:
            os.waitpid(s.pid, os.WNOHANG)
        except (ProcessLookupError, ChildProcessError):
            pass
        if s.pty_master_fd is not None:
            try:
                os.close(s.pty_master_fd)
            except OSError:
                pass

    def list(self) -> list[Session]:
        return list(self._sessions.values())

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def save_state(self) -> None:
        StatePersistence(self.state_path).save(list(self._sessions.values()))

    def restart_persisted(self) -> None:
        records = StatePersistence(self.state_path).load()
        for rec in records:
            try:
                self._spawn_resumed(
                    project_path=Path(rec["project_path"]),
                    name=rec["name"],
                    claude_session_id=rec.get("claude_session_id"),
                )
            except Exception as e:
                # Don't let one bad record block the others
                import logging
                logging.getLogger(__name__).warning(
                    "failed to resume session %s: %s", rec.get("name"), e
                )

    def _spawn_resumed(
        self,
        project_path: Path,
        name: str,
        claude_session_id: str | None,
    ) -> Session:
        if any(s.name == name for s in self._sessions.values()):
            return  # already running

        master_fd, slave_fd = pty.openpty()
        env = {**os.environ, "TERM": "xterm-256color"}
        cmd = list(self.claude_command) + [
            "--dangerously-skip-permissions",
            "--name", name,
        ]
        default_dir = Path.home() / ".claude" / "projects"
        if self.claude_projects_dir != default_dir:
            cmd += ["--projects-dir", str(self.claude_projects_dir)]
        if claude_session_id:
            cmd += ["--resume", claude_session_id]
        else:
            cmd += ["--continue"]

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(project_path),
                env=env,
                stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
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
            claude_session_id=claude_session_id,
            state=SessionState.BOOTSTRAPPING,
        )
        self._sessions[session.id] = session
        return session
