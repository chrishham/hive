from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

try:
    import anthropic
except ImportError:
    anthropic = None  # type: ignore[assignment]

logger = logging.getLogger("hive.synopsis")


def _claude_projects_dir() -> Path:
    """Get Claude projects directory dynamically (needed for testing with monkeypatched HOME)."""
    return Path.home() / ".claude" / "projects"


def _encode_project_path(project_path: str) -> str:
    resolved = str(Path(project_path).resolve())
    return resolved.replace("/", "-")


def extract_conversation(
    project_path: str,
    session_id: str,
    max_chars: int = 4000,
) -> list[dict[str, str]]:
    encoded = _encode_project_path(project_path)
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


def _synopsis_cache_dir() -> Path:
    return Path.home() / ".claude" / "hive" / "synopsis"


def load_cached_synopsis(session_name: str, jsonl_mtime: float, jsonl_size: int) -> str | None:
    cache_path = _synopsis_cache_dir() / f"{session_name}.json"
    if not cache_path.is_file():
        return None
    try:
        data = json.loads(cache_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("jsonl_mtime") != jsonl_mtime or data.get("jsonl_size") != jsonl_size:
        return None
    synopsis = data.get("synopsis")
    return synopsis if isinstance(synopsis, str) else None


def save_cached_synopsis(
    session_name: str,
    session_id: str,
    jsonl_mtime: float,
    jsonl_size: int,
    synopsis: str,
) -> None:
    cache_dir = _synopsis_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{session_name}.json"
    data = {
        "session_id": session_id,
        "jsonl_mtime": jsonl_mtime,
        "jsonl_size": jsonl_size,
        "synopsis": synopsis,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    cache_path.write_text(json.dumps(data, indent=2))


def remove_cached_synopsis(session_name: str) -> None:
    cache_path = _synopsis_cache_dir() / f"{session_name}.json"
    try:
        cache_path.unlink(missing_ok=True)
    except OSError:
        pass


_client_cache: tuple | None = None


def _get_client():
    global _client_cache
    if _client_cache is None:
        _client_cache = build_client()
    return _client_cache


def _jsonl_path(project_path: str, session_id: str) -> Path:
    encoded = _encode_project_path(project_path)
    return _claude_projects_dir() / encoded / f"{session_id}.jsonl"


def _fallback_synopsis(project_path: str, session_id: str) -> str:
    messages = extract_conversation(project_path, session_id, max_chars=200)
    if not messages:
        return "(no conversation yet)"
    return messages[0]["text"][:200]


async def get_synopsis(
    session_name: str,
    project_path: str,
    session_id: str,
) -> str:
    jpath = _jsonl_path(project_path, session_id)
    if not jpath.is_file():
        return "(no conversation yet)"

    try:
        stat = jpath.stat()
    except OSError:
        return "(no conversation yet)"

    mtime = stat.st_mtime
    size = stat.st_size

    cached = load_cached_synopsis(session_name, mtime, size)
    if cached is not None:
        return cached

    client, model = _get_client()
    if client is None:
        return _fallback_synopsis(project_path, session_id)

    messages = extract_conversation(project_path, session_id)
    if not messages:
        return "(no conversation yet)"

    try:
        synopsis = await asyncio.to_thread(
            generate_synopsis_text, client, model, messages
        )
    except Exception:
        logger.warning("synopsis generation failed for %s", session_name, exc_info=True)
        old_cached = _load_any_cached_synopsis(session_name)
        if old_cached is not None:
            return old_cached
        return _fallback_synopsis(project_path, session_id)

    save_cached_synopsis(session_name, session_id, mtime, size, synopsis)
    return synopsis


def _load_any_cached_synopsis(session_name: str) -> str | None:
    cache_path = _synopsis_cache_dir() / f"{session_name}.json"
    if not cache_path.is_file():
        return None
    try:
        data = json.loads(cache_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    synopsis = data.get("synopsis") if isinstance(data, dict) else None
    return synopsis if isinstance(synopsis, str) else None
