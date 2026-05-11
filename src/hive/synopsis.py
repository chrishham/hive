from __future__ import annotations

import json
from pathlib import Path


def _claude_projects_dir() -> Path:
    """Get Claude projects directory dynamically (needed for testing with monkeypatched HOME)."""
    return Path.home() / ".claude" / "projects"


def extract_conversation(
    project_path: str,
    session_id: str,
    max_chars: int = 4000,
) -> list[dict[str, str]]:
    encoded = project_path.replace("/", "-")
    jsonl_path = _claude_projects_dir() / encoded / f"{session_id}.jsonl"
    if not jsonl_path.is_file():
        return []

    all_msgs: list[dict[str, str]] = []
    try:
        with open(jsonl_path) as fp:
            for line in fp:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg_type = d.get("type")
                if msg_type not in ("user", "assistant"):
                    continue
                msg = d.get("message", {})
                if not isinstance(msg, dict):
                    continue
                content = msg.get("content", "")
                text = _extract_text(content)
                if text:
                    all_msgs.append({"role": msg_type, "text": text})
    except OSError:
        return []

    if not all_msgs:
        return []

    first = [all_msgs[0]]
    tail = all_msgs[1:][-19:]
    selected = first + tail

    total = 0
    result: list[dict[str, str]] = []
    for m in selected:
        remaining = max_chars - total
        if remaining <= 0:
            break
        text = m["text"][:remaining]
        result.append({"role": m["role"], "text": text})
        total += len(text)

    return result


def _extract_text(content) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                text = c.get("text", "")
                if isinstance(text, str) and text.strip():
                    return text.strip()
    return ""
