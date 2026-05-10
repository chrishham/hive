from __future__ import annotations

import argparse
import logging
from pathlib import Path

import uvicorn

from hive.server import build_app


def main() -> None:
    parser = argparse.ArgumentParser(prog="hive")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--state-path",
        type=Path,
        default=Path.home() / ".config" / "hive" / "state.json",
    )
    parser.add_argument(
        "--claude-projects-dir",
        type=Path,
        default=Path.home() / ".claude" / "projects",
    )
    parser.add_argument(
        "--claude-cmd",
        default="claude",
        help="claude binary name or path (default: claude on PATH)",
    )
    parser.add_argument(
        "--projects-root",
        type=Path,
        default=Path.home() / "Projects",
        help="Directory listed in the 'New session' picker (default: ~/Projects)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    static_dir = Path(__file__).parent / "static"
    app = build_app(
        claude_command=[args.claude_cmd],
        claude_projects_dir=args.claude_projects_dir,
        state_path=args.state_path,
        static_dir=static_dir,
        projects_root=args.projects_root,
    )

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
