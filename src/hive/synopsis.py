from __future__ import annotations

import json
import os
from pathlib import Path

try:
    import anthropic
except ImportError:
    anthropic = None  # type: ignore[assignment]


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


def build_client():
    if anthropic is None:
        return None, None

    if os.environ.get("CLAUDE_CODE_USE_VERTEX") == "1":
        project_id = os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID", "")
        region = os.environ.get("CLOUD_ML_REGION", "us-east5")
        client = anthropic.AnthropicVertex(project_id=project_id, region=region)
        return client, "claude-haiku-4-5@20251001"

    if os.environ.get("ANTHROPIC_FOUNDRY_API_KEY"):
        client = anthropic.AnthropicFoundry()
        return client, "claude-haiku-4-5"

    if os.environ.get("ANTHROPIC_API_KEY"):
        client = anthropic.Anthropic()
        return client, "claude-haiku-4-5-20251001"

    return None, None


_SYSTEM_PROMPT = (
    "Summarize this Claude Code session in 2-3 concise lines. "
    "State what the session is about, what has been accomplished, "
    "and what state it's in (e.g., waiting for input, actively working, debugging). "
    "Be specific about the actual work, not generic."
)


def generate_synopsis_text(client, model: str, messages: list[dict[str, str]]) -> str:
    if not messages:
        return ""
    conversation = "\n".join(f"[{m['role']}]: {m['text']}" for m in messages)
    response = client.messages.create(
        model=model,
        max_tokens=256,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": conversation}],
    )
    return response.content[0].text.strip()
