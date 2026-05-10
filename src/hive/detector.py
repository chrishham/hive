from __future__ import annotations

import json
import os
import re
import socket
from enum import Enum
from pathlib import Path


class SessionState(Enum):
    WAITING = "waiting"
    WORKING = "working"


PROMPT_PATTERNS = [
    re.compile(r"[❯>]\s*$", re.MULTILINE),
    re.compile(r"╭.*╮.*│.*>.*│.*╰.*╯", re.DOTALL),
    re.compile(r"│\s*>\s*│", re.MULTILINE),
]

WORKING_PATTERNS = [
    re.compile(r"[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]"),
    re.compile(r"Thinking\.\.\."),
    re.compile(r"Running:"),
    re.compile(r"Reading:"),
    re.compile(r"Editing:"),
    re.compile(r"Writing:"),
]

MODEL_PATTERN = re.compile(r"(Opus|Sonnet|Haiku)\s+(\d+\.\d+)(?:\s+\((\d+[KMG])\s+context\))?", re.IGNORECASE)

URL_PATTERN = re.compile(r"(?:https?://)?(?:localhost|127\.0\.0\.1):(\d{2,5})")

CONTEXT_PCT_PATTERN = re.compile(r"ctx:[█░]+ (\d+)%")


def detect_state(pane_text: str) -> SessionState:
    if not pane_text.strip():
        return SessionState.WORKING

    last_chunk = pane_text[-500:]

    for pattern in WORKING_PATTERNS:
        if pattern.search(last_chunk):
            return SessionState.WORKING

    for pattern in PROMPT_PATTERNS:
        if pattern.search(last_chunk):
            return SessionState.WAITING

    return SessionState.WORKING


def detect_model(pane_text: str) -> tuple[str | None, str | None]:
    match = MODEL_PATTERN.search(pane_text)
    if not match:
        return None, None
    name = match.group(1).lower()
    version = match.group(2)
    context = match.group(3)
    return f"{name}-{version}", context


def detect_context_pct_from_pane(pane_text: str) -> int | None:
    match = CONTEXT_PCT_PATTERN.search(pane_text[-500:])
    if match:
        return min(int(match.group(1)), 100)
    return None


def detect_urls(scrollback: str) -> list[str]:
    matches = URL_PATTERN.findall(scrollback)
    seen: set[str] = set()
    urls: list[str] = []
    for port in matches:
        key = f"localhost:{port}"
        if key not in seen:
            seen.add(key)
            urls.append(key)
    return urls


CONTEXT_WINDOWS = {
    "opus-4.7": 200_000,
    "opus-4.6": 200_000,
    "sonnet-4.6": 200_000,
    "haiku-4.5": 200_000,
}

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"


def _encode_project_path(project_path: str) -> str:
    return project_path.replace("/", "-")


def detect_context_usage(project_path: str, model: str | None, context_str: str | None) -> int | None:
    encoded = _encode_project_path(project_path)
    project_dir = CLAUDE_PROJECTS_DIR / encoded
    if not project_dir.is_dir():
        return None

    jsonl_files = sorted(project_dir.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not jsonl_files:
        return None

    last_usage = None
    try:
        with open(jsonl_files[0]) as f:
            for line in f:
                try:
                    d = json.loads(line)
                    if d.get("type") == "assistant":
                        usage = d.get("message", {}).get("usage")
                        if usage:
                            last_usage = usage
                except json.JSONDecodeError:
                    continue
    except OSError:
        return None

    if not last_usage:
        return None

    total_input = (
        last_usage.get("input_tokens", 0)
        + last_usage.get("cache_creation_input_tokens", 0)
        + last_usage.get("cache_read_input_tokens", 0)
    )
    total = total_input + last_usage.get("output_tokens", 0)

    if context_str:
        multipliers = {"K": 1_000, "M": 1_000_000, "G": 1_000_000_000}
        suffix = context_str[-1].upper()
        window = int(context_str[:-1]) * multipliers.get(suffix, 1)
    elif model and model in CONTEXT_WINDOWS:
        window = CONTEXT_WINDOWS[model]
    else:
        return None

    return min(round(total / window * 100), 100)


def probe_url(host_port: str, timeout: float = 0.5) -> bool:
    try:
        host, port_str = host_port.rsplit(":", 1)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, int(port_str)))
        sock.close()
        return result == 0
    except (ValueError, OSError):
        return False
