from __future__ import annotations

import os
import re
import socket
from enum import Enum


class SessionState(Enum):
    WAITING = "waiting"
    WORKING = "working"
    BOOTSTRAPPING = "bootstrapping"


PROMPT_PATTERNS = [
    re.compile(r"[❯>]\s*$", re.MULTILINE),
    re.compile(r"╭.*╮.*│.*>.*│.*╰.*╯", re.DOTALL),
    re.compile(r"│\s*>\s*│", re.MULTILINE),
    re.compile(r"❯❯\s+(bypass permissions|plan|default)", re.MULTILINE),
    re.compile(r"\(shift\+tab to cycle\)"),
]

WORKING_PATTERNS = [
    re.compile(r"[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]"),
    re.compile(r"\(\d+s\s*·"),
    re.compile(r"esc to interrupt"),
    re.compile(r"Thinking\.\.\."),
    re.compile(r"Running:"),
    re.compile(r"Reading:"),
    re.compile(r"Editing:"),
    re.compile(r"Writing:"),
]

MODEL_PATTERN = re.compile(r"(Opus|Sonnet|Haiku)\s+(\d+\.\d+)(?:\s+\((\d+[KMG])\s+context\))?", re.IGNORECASE)

URL_PATTERN = re.compile(r"(?:https?://)?(?:localhost|127\.0\.0\.1):(\d{2,5})")

CONTEXT_PCT_PATTERN = re.compile(r"ctx:[█░]+ (\d+)%")

LOADED_PATTERNS = [
    re.compile(r"Claude Code"),
    re.compile(r"Welcome back"),
    re.compile(r"Tips for getting started"),
]


def detect_state(pane_text: str) -> SessionState:
    if not pane_text.strip():
        return SessionState.BOOTSTRAPPING

    loaded = any(p.search(pane_text) for p in LOADED_PATTERNS)
    if not loaded:
        return SessionState.BOOTSTRAPPING

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
