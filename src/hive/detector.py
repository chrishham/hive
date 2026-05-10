from __future__ import annotations

import re
import socket
from enum import Enum


class SessionState(Enum):
    WAITING = "waiting"
    WORKING = "working"
    IDLE = "idle"
    EXITED = "exited"


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


def detect_state(pane_text: str) -> SessionState:
    if not pane_text.strip():
        return SessionState.EXITED

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
