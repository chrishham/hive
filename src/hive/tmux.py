from __future__ import annotations

import shlex
import subprocess

from hive.safety import TmuxError


class TmuxClient:
    def __init__(self, session_name: str = "hive"):
        self.session_name = session_name

    def _run(self, args: list[str], text: bool = False) -> subprocess.CompletedProcess:
        return subprocess.run(args, capture_output=True, text=text)

    def session_exists(self) -> bool:
        result = self._run(["tmux", "has-session", "-t", self.session_name])
        return result.returncode == 0

    def create_session(self, window_name: str | None = None, command: str | None = None) -> None:
        args = ["tmux", "new-session", "-d", "-s", self.session_name, "-x", "200", "-y", "50"]
        if window_name:
            args.extend(["-n", window_name])
        if command:
            args.append(command)
        self._run(args)
        self._run(["tmux", "set-option", "-t", self.session_name, "mouse", "on"])

    def list_windows(self) -> list[dict]:
        result = self._run(
            [
                "tmux", "list-windows", "-t", self.session_name,
                "-F", "#{window_index}\t#{window_name}\t#{pane_pid}",
            ],
            text=True,
        )
        if result.returncode != 0:
            return []
        windows = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            try:
                idx = int(parts[0])
                pid = int(parts[2])
            except ValueError:
                continue
            windows.append({
                "index": idx,
                "name": parts[1],
                "alive": pid > 0,
            })
        return windows

    def capture_pane(self, window_index: int) -> str:
        result = self._run(
            ["tmux", "capture-pane", "-t", f"{self.session_name}:{window_index}", "-p", "-S", "-100"],
            text=True,
        )
        return result.stdout if result.returncode == 0 else ""

    def capture_pane_scrollback(self, window_index: int, lines: int = 1000) -> str:
        result = self._run(
            [
                "tmux", "capture-pane",
                "-t", f"{self.session_name}:{window_index}",
                "-p", "-S", f"-{lines}",
            ],
            text=True,
        )
        return result.stdout if result.returncode == 0 else ""

    def new_window(
        self,
        name: str,
        cwd: str,
        command_args: list[str],
        env: dict[str, str] | None = None,
    ) -> int:
        args = [
            "tmux", "new-window", "-t", f"{self.session_name}:",
            "-n", name,
            "-c", cwd,
        ]
        if env:
            for k, v in env.items():
                args.extend(["-e", f"{k}={v}"])
        args.extend(["-P", "-F", "#{window_index}", shlex.join(command_args)])
        result = self._run(args, text=True)
        if result.returncode != 0:
            stderr = (result.stderr or "").strip() if hasattr(result, "stderr") else ""
            raise TmuxError(f"tmux new-window failed (rc={result.returncode}): {stderr}")
        try:
            return int(result.stdout.strip())
        except ValueError as exc:
            raise TmuxError(
                f"tmux new-window returned non-numeric output: {result.stdout!r}"
            ) from exc

    def kill_window(self, window_index: int) -> None:
        self._run(["tmux", "kill-window", "-t", f"{self.session_name}:{window_index}"])

    def select_window(self, window_index: int) -> None:
        self._run(["tmux", "select-window", "-t", f"{self.session_name}:{window_index}"])

    def is_pane_alive(self, window_index: int) -> bool:
        result = self._run(
            ["tmux", "display-message", "-t", f"{self.session_name}:{window_index}", "-p", "#{pane_pid}"],
            text=True,
        )
        if result.returncode != 0:
            return False
        # Non-zero pid means alive, 0 means dead
        return result.stdout.strip() != "0"

    def set_status_bar(self, text: str) -> None:
        self._run(["tmux", "set-option", "-t", self.session_name, "status-left-length", "100"])
        self._run(["tmux", "set-option", "-t", self.session_name, "status-left", text])

    def set_window_option(self, window_index: int, option: str, value: str) -> None:
        self._run([
            "tmux", "set-window-option",
            "-t", f"{self.session_name}:{window_index}",
            option, value,
        ])

    def rename_window(self, window_index: int, new_name: str) -> None:
        self._run(["tmux", "rename-window", "-t", f"{self.session_name}:{window_index}", new_name])

    def send_keys(self, window_index: int, keys: str) -> None:
        self._run(["tmux", "send-keys", "-t", f"{self.session_name}:{window_index}", keys, "Enter"])

    def kill_session(self) -> None:
        self._run(["tmux", "kill-session", "-t", self.session_name])

    def attach(self) -> None:
        subprocess.run(["tmux", "attach-session", "-t", self.session_name])
