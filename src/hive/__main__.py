from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

from hive.config import HiveConfig
from hive.tmux import TmuxClient

DASHBOARD_LOG = Path.home() / ".claude" / "hive" / "dashboard.log"


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
            status = "alive" if win["active"] else "dead"
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
        _ensure_session(tmux, config)
        name = _unique_window_name(tmux, os.path.basename(path))
        tmux.new_window(
            name, path,
            f"claude --dangerously-skip-permissions --name {name}",
            env={"HIVE_SESSION": name},
        )
        print(f"Created session '{name}' in {path}")
        return

    if command and command != "":
        print(f"Unknown command: {command}")
        print("Usage: hive [attach|list|new <path>|install-hooks]")
        sys.exit(1)

    if os.environ.get("TMUX"):
        from hive.app import HiveApp
        app = HiveApp()
        app.run()
    else:
        if not tmux.session_exists():
            DASHBOARD_LOG.parent.mkdir(parents=True, exist_ok=True)
            dashboard_cmd = f"uv run python -m hive 2>> {shlex.quote(str(DASHBOARD_LOG))}"
            tmux.create_session(window_name="dashboard", command=dashboard_cmd)
            tmux.set_window_option(0, "remain-on-exit", "on")
        tmux.attach()


def _ensure_session(tmux: TmuxClient, config: HiveConfig) -> None:
    if not tmux.session_exists():
        tmux.create_session()


def _unique_window_name(tmux: TmuxClient, base: str) -> str:
    existing = {w["name"] for w in tmux.list_windows()}
    if base not in existing:
        return base
    i = 2
    while f"{base}-{i:03d}" in existing:
        i += 1
    return f"{base}-{i:03d}"


if __name__ == "__main__":
    main()
