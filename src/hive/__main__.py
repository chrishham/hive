from __future__ import annotations

import os
import sys

from hive.config import HiveConfig
from hive.tmux import TmuxClient


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

    if command == "new" and len(args) >= 2:
        path = os.path.abspath(args[1])
        if not os.path.isdir(path):
            print(f"Directory not found: {path}")
            sys.exit(1)
        _ensure_session(tmux, config)
        name = os.path.basename(path)
        tmux.new_window(name, path, f"claude --name {name}")
        print(f"Created session '{name}' in {path}")
        return

    if command and command != "":
        print(f"Unknown command: {command}")
        print("Usage: hive [attach|list|new <path>]")
        sys.exit(1)

    if os.environ.get("TMUX"):
        from hive.app import HiveApp
        app = HiveApp()
        app.run()
    else:
        if not tmux.session_exists():
            tmux.create_session(window_name="dashboard", command="uv run python -m hive")
        tmux.attach()


def _ensure_session(tmux: TmuxClient, config: HiveConfig) -> None:
    if not tmux.session_exists():
        tmux.create_session()


if __name__ == "__main__":
    main()
