"""Fake claude binary for tests.

Behavior: writes a single 'assistant end_turn' event to the JSONL Claude
Code would normally write to (~/.claude/projects/<encoded-cwd>/<uuid>.jsonl),
prints a banner to stdout, then sleeps until killed.

Honors --name, --resume, --continue, --dangerously-skip-permissions just
enough to be a drop-in for SessionManager tests.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


def encode(p: str) -> str:
    return p.replace("/", "-")


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--name", default="fake")
    parser.add_argument("--resume", default=None)
    parser.add_argument("--continue", action="store_true", dest="cont")
    parser.add_argument(
        "--dangerously-skip-permissions", action="store_true"
    )
    parser.add_argument(
        "--projects-dir", default=str(Path.home() / ".claude" / "projects")
    )
    args, _ = parser.parse_known_args()

    sid = args.resume or str(uuid.uuid4())
    cwd = os.getcwd()
    outdir = Path(args.projects_dir) / encode(cwd)
    outdir.mkdir(parents=True, exist_ok=True)
    jsonl = outdir / f"{sid}.jsonl"

    now = datetime.now(timezone.utc).isoformat()
    with jsonl.open("a") as f:
        f.write(json.dumps({
            "type": "assistant",
            "timestamp": now,
            "message": {
                "stop_reason": "end_turn",
                "model": "claude-opus-4-7",
                "content": [{"type": "text", "text": "ready"}],
                "usage": {"input_tokens": 100, "output_tokens": 50},
            },
        }) + "\n")

    sys.stdout.write(f"fake-claude session={sid} name={args.name}\n")
    sys.stdout.flush()

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
