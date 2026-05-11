from __future__ import annotations

import os
import sys
from pathlib import Path

from hive.config import HiveConfig
from hive.safety import InvalidSessionName, TmuxError, sanitize_session_name, validate_session_name
from hive.tmux import TmuxClient

DASHBOARD_LOG = Path.home() / ".claude" / "hive" / "dashboard.log"
DASHBOARD_LOG_MAX_BYTES = 5 * 1024 * 1024


def _rotate_dashboard_log() -> None:
    try:
        st = os.lstat(DASHBOARD_LOG)
    except FileNotFoundError:
        return
    import stat as _stat
    if not _stat.S_ISREG(st.st_mode):
        # Symlink or other non-regular file: remove so O_NOFOLLOW open succeeds
        # (and to refuse the attacker's chosen target).
        try:
            os.unlink(DASHBOARD_LOG)
        except OSError:
            pass
        return
    if st.st_size > DASHBOARD_LOG_MAX_BYTES:
        rotated = DASHBOARD_LOG.with_suffix(".log.1")
        try:
            os.replace(DASHBOARD_LOG, rotated)
        except OSError:
            pass


def main() -> None:
    config = HiveConfig.load_default()
    tmux = TmuxClient(config.tmux_session_name)

    args = sys.argv[1:]
    command = args[0] if args else ""

    if command == "attach":
        if not tmux.session_exists():
            print(f"No hive session '{config.tmux_session_name}' found. Run 'hive' first.")
            sys.exit(1)
        tmux.attach()
        return

    if command == "list":
        if not tmux.session_exists():
            print("No hive session running.")
            sys.exit(1)
        windows = tmux.list_windows()
        for win in windows:
            if win["index"] == 0:
                continue
            status = "alive" if win["alive"] else "dead"
            print(f"  {win['name']}  (window {win['index']}, {status})")
        return

    if command == "install-hooks":
        from hive.install_hooks import install_hooks, settings_path
        if install_hooks():
            print(f"Installed hive hooks into {settings_path()}")
            return
        print(f"Could not install hooks (corrupt {settings_path()}?)")
        sys.exit(1)

    if command == "new" and len(args) >= 2:
        path = os.path.abspath(args[1])
        if not os.path.isdir(path):
            print(f"Directory not found: {path}")
            sys.exit(1)
        _ensure_session(tmux)
        name = _unique_window_name(tmux, os.path.basename(path))
        try:
            validate_session_name(name)
        except InvalidSessionName as exc:
            print(f"Refusing unsafe session name: {exc}")
            sys.exit(1)
        try:
            tmux.new_window(
                name, path,
                ["claude", "--dangerously-skip-permissions", "--name", name],
                env={"HIVE_SESSION": name},
            )
        except TmuxError as exc:
            print(f"tmux failed: {exc}")
            sys.exit(1)
        print(f"Created session '{name}' in {path}")
        return

    if command and command != "":
        print(f"Unknown command: {command}")
        print("Usage: hive [attach|list|new <path>|install-hooks]")
        sys.exit(1)

    if os.environ.get("TMUX"):
        DASHBOARD_LOG.parent.mkdir(parents=True, exist_ok=True)
        _rotate_dashboard_log()
        # Textual writes the TUI to sys.__stderr__; reassign sys.stderr so our
        # logging/tracebacks land in the file without hijacking Textual's output.
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
        try:
            log_fd = os.open(DASHBOARD_LOG, flags, 0o600)
        except OSError:
            log_fd = None
        if log_fd is not None:
            sys.stderr = os.fdopen(log_fd, "a", buffering=1)
        from hive.app import HiveApp
        app = HiveApp()
        app.run()
    else:
        if not tmux.session_exists():
            tmux.create_session(window_name="dashboard", command="uv run python -m hive")
            tmux.set_window_option(0, "remain-on-exit", "on")
        tmux.attach()


def _ensure_session(tmux: TmuxClient) -> None:
    if not tmux.session_exists():
        tmux.create_session()


def _unique_window_name(tmux: TmuxClient, base: str) -> str:
    base = sanitize_session_name(base)
    existing = {w["name"] for w in tmux.list_windows()}
    if base not in existing:
        return base
    i = 2
    while f"{base}-{i:03d}" in existing:
        i += 1
    return f"{base}-{i:03d}"


if __name__ == "__main__":
    main()
