# Session Synopsis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the raw tmux pane preview with an LLM-generated session synopsis, cached to disk and refreshed when the conversation changes.

**Architecture:** A new `synopsis.py` module extracts conversation messages from Claude Code's JSONL files and sends them to Haiku for summarization. Results are cached to `~/.claude/hive/synopsis/` keyed by JSONL mtime+size. The dashboard's refresh loop calls synopsis generation asynchronously, and the preview pane displays the synopsis text. Provider auto-detection (Vertex, Foundry, direct API) inherits from the same env vars Claude Code uses.

**Tech Stack:** Python 3.11+, `anthropic` SDK (AnthropicVertex / AnthropicFoundry / Anthropic clients), Textual TUI framework, asyncio

---

## File Structure

| File | Responsibility |
|------|----------------|
| `src/hive/synopsis.py` | New: conversation extraction, provider detection, LLM call, disk caching, public async API |
| `src/hive/widgets/preview.py` | Modify: simplify to display synopsis string, remove ANSI/box cleanup |
| `src/hive/widgets/session_list.py` | Modify: replace `preview_text` with `synopsis` field |
| `src/hive/app.py` | Modify: integrate synopsis into refresh loop, update preview, cleanup on kill |
| `src/hive/hook_state.py` | Modify: add `read_session_id()` helper to extract session_id from hook state |
| `pyproject.toml` | Modify: add `anthropic` dependency |
| `tests/test_synopsis.py` | New: tests for extraction, caching, provider detection |

---

### Task 1: Add `anthropic` dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add anthropic to dependencies**

In `pyproject.toml`, add `anthropic` to the `dependencies` list:

```toml
dependencies = [
    "textual>=3.0",
    "tomli>=2.0; python_version < '3.12'",
    "anthropic>=0.42",
]
```

- [ ] **Step 2: Install updated dependencies**

Run: `cd /mnt/data/projects/hive && uv sync`
Expected: Clean install, anthropic package available.

- [ ] **Step 3: Verify import works**

Run: `cd /mnt/data/projects/hive && uv run python -c "import anthropic; print(anthropic.__version__)"`
Expected: Prints a version number without error.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "feat(synopsis): add anthropic SDK dependency"
```

---

### Task 2: Add `read_session_id()` to hook_state

**Files:**
- Modify: `src/hive/hook_state.py`
- Test: `tests/test_hook_state.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_hook_state.py`:

```python
from hive.hook_state import read_session_id


def test_read_session_id_returns_id(fake_home):
    _write_state(fake_home, "foo", {"state": "working", "session_id": "abc-123"})
    assert read_session_id("foo") == "abc-123"


def test_read_session_id_missing_file_returns_none(fake_home):
    assert read_session_id("nope") is None


def test_read_session_id_no_session_id_field(fake_home):
    _write_state(fake_home, "foo", {"state": "working"})
    assert read_session_id("foo") is None


def test_read_session_id_unsafe_name_returns_none(fake_home):
    assert read_session_id("../escape") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /mnt/data/projects/hive && uv run pytest tests/test_hook_state.py -v -k "read_session_id"`
Expected: FAIL — `ImportError: cannot import name 'read_session_id'`

- [ ] **Step 3: Implement `read_session_id`**

Add to `src/hive/hook_state.py`, after the `read_session_state_with_meta` function:

```python
def read_session_id(name: str) -> str | None:
    try:
        path = state_file_path(name)
    except ValueError:
        return None
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    sid = data.get("session_id")
    return sid if isinstance(sid, str) and sid else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /mnt/data/projects/hive && uv run pytest tests/test_hook_state.py -v`
Expected: All pass, including the 4 new tests.

- [ ] **Step 5: Commit**

```bash
git add src/hive/hook_state.py tests/test_hook_state.py
git commit -m "feat(synopsis): add read_session_id to hook_state"
```

---

### Task 3: Implement `synopsis.py` — conversation extraction

**Files:**
- Create: `src/hive/synopsis.py`
- Create: `tests/test_synopsis.py`

- [ ] **Step 1: Write the failing tests for conversation extraction**

Create `tests/test_synopsis.py`:

```python
import json
import pytest

from hive.synopsis import extract_conversation, CLAUDE_PROJECTS_DIR


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def _make_jsonl(home, project_path, session_id, messages):
    """Write a JSONL conversation file. messages is a list of (role, text) tuples."""
    encoded = project_path.replace("/", "-")
    proj_dir = home / ".claude" / "projects" / encoded
    proj_dir.mkdir(parents=True, exist_ok=True)
    path = proj_dir / f"{session_id}.jsonl"
    lines = []
    for role, text in messages:
        entry = {
            "type": role,
            "message": {"content": [{"type": "text", "text": text}]},
        }
        lines.append(json.dumps(entry))
    path.write_text("\n".join(lines))
    return path


def test_extract_basic_conversation(fake_home):
    _make_jsonl(fake_home, "/home/user/project", "sess-1", [
        ("user", "Fix the login bug"),
        ("assistant", "I'll look at the auth module."),
        ("user", "Also check the session timeout"),
        ("assistant", "Found the issue in session.py."),
    ])
    messages = extract_conversation("/home/user/project", "sess-1")
    assert len(messages) == 4
    assert messages[0] == {"role": "user", "text": "Fix the login bug"}
    assert messages[1] == {"role": "assistant", "text": "I'll look at the auth module."}


def test_extract_skips_non_user_assistant(fake_home):
    encoded = "/home/user/project".replace("/", "-")
    proj_dir = fake_home / ".claude" / "projects" / encoded
    proj_dir.mkdir(parents=True, exist_ok=True)
    path = proj_dir / "sess-2.jsonl"
    lines = [
        json.dumps({"type": "last-prompt", "leafUuid": "abc"}),
        json.dumps({"type": "permission-mode", "permissionMode": "default"}),
        json.dumps({"type": "user", "message": {"content": [{"type": "text", "text": "Hello"}]}}),
        json.dumps({"type": "attachment", "attachment": {"type": "hook_success"}}),
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Hi there"}]}}),
    ]
    path.write_text("\n".join(lines))
    messages = extract_conversation("/home/user/project", "sess-2")
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[1]["role"] == "assistant"


def test_extract_missing_file_returns_empty(fake_home):
    messages = extract_conversation("/nonexistent/path", "no-such-session")
    assert messages == []


def test_extract_caps_total_chars(fake_home):
    long_msgs = [("user", "x" * 3000), ("assistant", "y" * 3000)]
    _make_jsonl(fake_home, "/home/user/project", "sess-3", long_msgs)
    messages = extract_conversation("/home/user/project", "sess-3", max_chars=4000)
    total = sum(len(m["text"]) for m in messages)
    assert total <= 4000


def test_extract_keeps_first_and_last_messages(fake_home):
    msgs = [(("user" if i % 2 == 0 else "assistant"), f"msg-{i}") for i in range(30)]
    _make_jsonl(fake_home, "/home/user/project", "sess-4", msgs)
    messages = extract_conversation("/home/user/project", "sess-4")
    assert messages[0]["text"] == "msg-0"
    assert messages[-1]["text"] == "msg-29"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /mnt/data/projects/hive && uv run pytest tests/test_synopsis.py -v -k "extract"`
Expected: FAIL — `ModuleNotFoundError: No module named 'hive.synopsis'`

- [ ] **Step 3: Implement `extract_conversation`**

Create `src/hive/synopsis.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"


def extract_conversation(
    project_path: str,
    session_id: str,
    max_chars: int = 4000,
) -> list[dict[str, str]]:
    encoded = project_path.replace("/", "-")
    jsonl_path = CLAUDE_PROJECTS_DIR / encoded / f"{session_id}.jsonl"
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /mnt/data/projects/hive && uv run pytest tests/test_synopsis.py -v -k "extract"`
Expected: All 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/hive/synopsis.py tests/test_synopsis.py
git commit -m "feat(synopsis): implement conversation extraction from JSONL"
```

---

### Task 4: Implement `synopsis.py` — provider detection and LLM call

**Files:**
- Modify: `src/hive/synopsis.py`
- Modify: `tests/test_synopsis.py`

- [ ] **Step 1: Write failing tests for provider detection**

Add to `tests/test_synopsis.py`:

```python
from unittest.mock import patch, MagicMock
from hive.synopsis import build_client


def test_build_client_vertex(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_USE_VERTEX", "1")
    monkeypatch.setenv("ANTHROPIC_VERTEX_PROJECT_ID", "my-project-123")
    monkeypatch.setenv("CLOUD_ML_REGION", "us-east5")
    with patch("hive.synopsis.anthropic") as mock_anthropic:
        client, model = build_client()
        mock_anthropic.AnthropicVertex.assert_called_once_with(
            project_id="my-project-123",
            region="us-east5",
        )
        assert model == "claude-haiku-4-5@20251001"


def test_build_client_vertex_default_region(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_USE_VERTEX", "1")
    monkeypatch.setenv("ANTHROPIC_VERTEX_PROJECT_ID", "my-project-123")
    monkeypatch.delenv("CLOUD_ML_REGION", raising=False)
    with patch("hive.synopsis.anthropic") as mock_anthropic:
        client, model = build_client()
        mock_anthropic.AnthropicVertex.assert_called_once_with(
            project_id="my-project-123",
            region="us-east5",
        )


def test_build_client_foundry(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_USE_VERTEX", raising=False)
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_API_KEY", "key-123")
    with patch("hive.synopsis.anthropic") as mock_anthropic:
        client, model = build_client()
        mock_anthropic.AnthropicFoundry.assert_called_once()
        assert model == "claude-haiku-4-5"


def test_build_client_direct_api(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_USE_VERTEX", raising=False)
    monkeypatch.delenv("ANTHROPIC_FOUNDRY_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-123")
    with patch("hive.synopsis.anthropic") as mock_anthropic:
        client, model = build_client()
        mock_anthropic.Anthropic.assert_called_once()
        assert model == "claude-haiku-4-5-20251001"


def test_build_client_no_credentials(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_USE_VERTEX", raising=False)
    monkeypatch.delenv("ANTHROPIC_FOUNDRY_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client, model = build_client()
    assert client is None
    assert model is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /mnt/data/projects/hive && uv run pytest tests/test_synopsis.py -v -k "build_client"`
Expected: FAIL — `ImportError: cannot import name 'build_client'`

- [ ] **Step 3: Implement `build_client`**

Add to `src/hive/synopsis.py`, after the imports:

```python
import os

try:
    import anthropic
except ImportError:
    anthropic = None  # type: ignore[assignment]


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /mnt/data/projects/hive && uv run pytest tests/test_synopsis.py -v -k "build_client"`
Expected: All 5 tests pass.

- [ ] **Step 5: Write failing test for LLM summarization**

Add to `tests/test_synopsis.py`:

```python
def test_generate_synopsis_calls_llm():
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="Session is about fixing auth bugs. Made progress on token validation. Currently waiting for user input.")]
    mock_client.messages.create.return_value = mock_response

    from hive.synopsis import generate_synopsis_text
    messages = [
        {"role": "user", "text": "Fix the login bug"},
        {"role": "assistant", "text": "Looking at auth module now."},
    ]
    result = generate_synopsis_text(mock_client, "claude-haiku-4-5", messages)
    assert "auth" in result.lower()
    mock_client.messages.create.assert_called_once()
    call_kwargs = mock_client.messages.create.call_args[1]
    assert call_kwargs["model"] == "claude-haiku-4-5"
    assert call_kwargs["max_tokens"] == 256


def test_generate_synopsis_text_empty_messages():
    from hive.synopsis import generate_synopsis_text
    result = generate_synopsis_text(MagicMock(), "model", [])
    assert result == ""
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `cd /mnt/data/projects/hive && uv run pytest tests/test_synopsis.py -v -k "generate_synopsis"`
Expected: FAIL — `ImportError: cannot import name 'generate_synopsis_text'`

- [ ] **Step 7: Implement `generate_synopsis_text`**

Add to `src/hive/synopsis.py`:

```python
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
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd /mnt/data/projects/hive && uv run pytest tests/test_synopsis.py -v -k "generate_synopsis"`
Expected: All 2 tests pass.

- [ ] **Step 9: Commit**

```bash
git add src/hive/synopsis.py tests/test_synopsis.py
git commit -m "feat(synopsis): add provider detection and LLM summarization"
```

---

### Task 5: Implement `synopsis.py` — disk caching

**Files:**
- Modify: `src/hive/synopsis.py`
- Modify: `tests/test_synopsis.py`

- [ ] **Step 1: Write failing tests for caching**

Add to `tests/test_synopsis.py`:

```python
import time
from hive.synopsis import (
    _synopsis_cache_dir,
    load_cached_synopsis,
    save_cached_synopsis,
)


@pytest.fixture
def cache_dir(fake_home):
    d = fake_home / ".claude" / "hive" / "synopsis"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_save_and_load_cache(fake_home, cache_dir):
    save_cached_synopsis("my-session", "sess-id", 1000.0, 5000, "This is the synopsis.")
    result = load_cached_synopsis("my-session", 1000.0, 5000)
    assert result == "This is the synopsis."


def test_load_cache_stale_mtime(fake_home, cache_dir):
    save_cached_synopsis("my-session", "sess-id", 1000.0, 5000, "Old synopsis.")
    result = load_cached_synopsis("my-session", 2000.0, 5000)
    assert result is None


def test_load_cache_stale_size(fake_home, cache_dir):
    save_cached_synopsis("my-session", "sess-id", 1000.0, 5000, "Old synopsis.")
    result = load_cached_synopsis("my-session", 1000.0, 9999)
    assert result is None


def test_load_cache_missing(fake_home, cache_dir):
    result = load_cached_synopsis("no-such-session", 1000.0, 5000)
    assert result is None


def test_remove_cached_synopsis(fake_home, cache_dir):
    save_cached_synopsis("my-session", "sess-id", 1000.0, 5000, "synopsis")
    from hive.synopsis import remove_cached_synopsis
    remove_cached_synopsis("my-session")
    assert load_cached_synopsis("my-session", 1000.0, 5000) is None


def test_remove_cached_synopsis_idempotent(fake_home, cache_dir):
    from hive.synopsis import remove_cached_synopsis
    remove_cached_synopsis("nonexistent")  # must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /mnt/data/projects/hive && uv run pytest tests/test_synopsis.py -v -k "cache"`
Expected: FAIL — `ImportError: cannot import name '_synopsis_cache_dir'`

- [ ] **Step 3: Implement caching functions**

Add to `src/hive/synopsis.py`:

```python
from datetime import datetime, timezone


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /mnt/data/projects/hive && uv run pytest tests/test_synopsis.py -v -k "cache"`
Expected: All 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/hive/synopsis.py tests/test_synopsis.py
git commit -m "feat(synopsis): add disk caching for synopses"
```

---

### Task 6: Implement `synopsis.py` — public async API (`get_synopsis`)

**Files:**
- Modify: `src/hive/synopsis.py`
- Modify: `tests/test_synopsis.py`

- [ ] **Step 1: Write failing tests for `get_synopsis`**

Add to `tests/test_synopsis.py`:

```python
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from hive.synopsis import get_synopsis


def test_get_synopsis_returns_cached(fake_home, cache_dir):
    jsonl_path = _make_jsonl(fake_home, "/home/user/proj", "sess-1", [
        ("user", "Hello"),
        ("assistant", "Hi"),
    ])
    mtime = jsonl_path.stat().st_mtime
    size = jsonl_path.stat().st_size
    save_cached_synopsis("my-session", "sess-1", mtime, size, "Cached synopsis.")

    result = asyncio.run(get_synopsis("my-session", "/home/user/proj", "sess-1"))
    assert result == "Cached synopsis."


def test_get_synopsis_generates_when_no_cache(fake_home, cache_dir):
    _make_jsonl(fake_home, "/home/user/proj", "sess-1", [
        ("user", "Fix the bug"),
        ("assistant", "On it."),
    ])
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="Fixing a bug in the login flow.")]
    mock_client.messages.create.return_value = mock_response

    with patch("hive.synopsis.build_client", return_value=(mock_client, "haiku")):
        result = asyncio.run(get_synopsis("my-session", "/home/user/proj", "sess-1"))
    assert result == "Fixing a bug in the login flow."
    assert load_cached_synopsis(
        "my-session",
        (fake_home / ".claude" / "projects" / "-home-user-proj" / "sess-1.jsonl").stat().st_mtime,
        (fake_home / ".claude" / "projects" / "-home-user-proj" / "sess-1.jsonl").stat().st_size,
    ) == "Fixing a bug in the login flow."


def test_get_synopsis_no_credentials_fallback(fake_home, cache_dir):
    _make_jsonl(fake_home, "/home/user/proj", "sess-1", [
        ("user", "Help me refactor the database layer"),
    ])
    with patch("hive.synopsis.build_client", return_value=(None, None)):
        result = asyncio.run(get_synopsis("my-session", "/home/user/proj", "sess-1"))
    assert "refactor the database layer" in result


def test_get_synopsis_no_jsonl(fake_home, cache_dir):
    result = asyncio.run(get_synopsis("my-session", "/nonexistent", "no-sess"))
    assert result == "(no conversation yet)"


def test_get_synopsis_api_error_keeps_old_cache(fake_home, cache_dir):
    jsonl_path = _make_jsonl(fake_home, "/home/user/proj", "sess-1", [
        ("user", "Hello"),
    ])
    mtime1 = jsonl_path.stat().st_mtime
    size1 = jsonl_path.stat().st_size
    save_cached_synopsis("my-session", "sess-1", mtime1, size1, "Old cached synopsis.")

    # Append a new message to change mtime/size so cache is stale
    with open(jsonl_path, "a") as f:
        f.write("\n" + json.dumps({"type": "user", "message": {"content": [{"type": "text", "text": "new msg"}]}}))

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = Exception("API error")

    with patch("hive.synopsis.build_client", return_value=(mock_client, "haiku")):
        result = asyncio.run(get_synopsis("my-session", "/home/user/proj", "sess-1"))
    assert result == "Old cached synopsis."
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /mnt/data/projects/hive && uv run pytest tests/test_synopsis.py -v -k "get_synopsis"`
Expected: FAIL — `ImportError: cannot import name 'get_synopsis'`

- [ ] **Step 3: Implement `get_synopsis`**

Add to `src/hive/synopsis.py`:

```python
import asyncio
import logging
import sys

logger = logging.getLogger("hive.synopsis")

_client_cache: tuple | None = None


def _get_client():
    global _client_cache
    if _client_cache is None:
        _client_cache = build_client()
    return _client_cache


def _jsonl_path(project_path: str, session_id: str) -> Path:
    encoded = project_path.replace("/", "-")
    return CLAUDE_PROJECTS_DIR / encoded / f"{session_id}.jsonl"


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /mnt/data/projects/hive && uv run pytest tests/test_synopsis.py -v -k "get_synopsis"`
Expected: All 5 tests pass.

- [ ] **Step 5: Run full test suite**

Run: `cd /mnt/data/projects/hive && uv run pytest tests/test_synopsis.py -v`
Expected: All tests in the file pass (extraction + build_client + cache + get_synopsis).

- [ ] **Step 6: Commit**

```bash
git add src/hive/synopsis.py tests/test_synopsis.py
git commit -m "feat(synopsis): add async get_synopsis with caching and fallback"
```

---

### Task 7: Simplify `PreviewPane` widget

**Files:**
- Modify: `src/hive/widgets/preview.py`

- [ ] **Step 1: Rewrite `preview.py`**

Replace the entire contents of `src/hive/widgets/preview.py` with:

```python
from __future__ import annotations

from textual.widgets import Static


class PreviewPane(Static):
    DEFAULT_CSS = """
    PreviewPane {
        width: 1fr;
        height: 1fr;
        padding: 0 1;
        overflow-y: auto;
    }
    """

    def __init__(self, *args, **kwargs) -> None:
        kwargs.setdefault("markup", False)
        super().__init__(*args, **kwargs)

    def set_content(self, synopsis: str) -> None:
        self.update(synopsis if synopsis else "(no session selected)")

    def clear_content(self) -> None:
        self.update("(no session selected)")
```

- [ ] **Step 2: Verify no import errors**

Run: `cd /mnt/data/projects/hive && uv run python -c "from hive.widgets.preview import PreviewPane; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add src/hive/widgets/preview.py
git commit -m "refactor(preview): simplify PreviewPane for synopsis display"
```

---

### Task 8: Update `SessionData` to use `synopsis`

**Files:**
- Modify: `src/hive/widgets/session_list.py`

- [ ] **Step 1: Replace `preview_text` with `synopsis` in `SessionData`**

In `src/hive/widgets/session_list.py`, change the `SessionData` dataclass. Replace:

```python
    preview_text: str = ""
```

with:

```python
    synopsis: str = ""
```

- [ ] **Step 2: Verify no import errors**

Run: `cd /mnt/data/projects/hive && uv run python -c "from hive.widgets.session_list import SessionData; d = SessionData(name='test', project_path='/tmp', tmux_window=1); print(d.synopsis)"`
Expected: prints empty string

- [ ] **Step 3: Commit**

```bash
git add src/hive/widgets/session_list.py
git commit -m "refactor(session_list): replace preview_text with synopsis field"
```

---

### Task 9: Integrate synopsis into `app.py`

**Files:**
- Modify: `src/hive/app.py`

- [ ] **Step 1: Add synopsis imports**

At the top of `src/hive/app.py`, add to the imports:

```python
from hive.hook_state import read_session_id, read_session_state_with_meta, remove_session_state
from hive.synopsis import get_synopsis, remove_cached_synopsis
```

Update the existing `from hive.hook_state import ...` line to include `read_session_id`. The final import line should be:

```python
from hive.hook_state import read_session_id, read_session_state_with_meta, remove_session_state
```

- [ ] **Step 2: Add synopsis tracking state to `HiveApp.__init__` or class body**

Find the `session_data_map` initialization in `HiveApp` and add nearby:

```python
        self._synopsis_in_flight: set[str] = set()
```

Locate where `self.session_data_map` is initialized (in `on_mount` or `__init__`) and add `self._synopsis_in_flight = set()` right after it.

- [ ] **Step 3: Update `_refresh_sessions` to use synopsis**

In the `_refresh_sessions` method, change the `SessionData` construction. Replace:

```python
                preview_text=pane_text,
```

with:

```python
                synopsis=self.session_data_map.get(name, SessionData(name=name, project_path="", tmux_window=0)).synopsis,
```

After `self.session_data_map[name] = data` (line ~480), add the async synopsis fetch:

```python
            session_id = read_session_id(name)
            if session_id and name not in self._synopsis_in_flight:
                self._synopsis_in_flight.add(name)
                self._fetch_synopsis(name, project_path, session_id)
```

- [ ] **Step 4: Add `_fetch_synopsis` worker method**

Add this method to `HiveApp`:

```python
    @work
    async def _fetch_synopsis(self, session_name: str, project_path: str, session_id: str) -> None:
        try:
            synopsis = await get_synopsis(session_name, project_path, session_id)
            if session_name in self.session_data_map:
                self.session_data_map[session_name].synopsis = synopsis
        except Exception:
            pass
        finally:
            self._synopsis_in_flight.discard(session_name)
```

- [ ] **Step 5: Update `_update_preview`**

Replace the `_update_preview` method:

```python
    def _update_preview(self, list_view: SessionListView, preview: PreviewPane) -> None:
        data = list_view.get_session_data()
        if data:
            preview.set_content(data.synopsis or "(generating synopsis...)")
        else:
            preview.clear_content()
```

- [ ] **Step 6: Add synopsis cleanup to `action_kill_session`**

In the `action_kill_session` method, after `remove_session_state(data.name)` (line ~746), add:

```python
            remove_cached_synopsis(data.name)
```

- [ ] **Step 7: Remove `preview_lines` from config usage**

The `_update_preview` method no longer uses `self.config.preview_lines`. Optionally remove `preview_lines` from `HiveConfig` in `config.py` if desired, or leave it for now (harmless).

- [ ] **Step 8: Verify the app still loads**

Run: `cd /mnt/data/projects/hive && uv run python -c "from hive.app import HiveApp; print('ok')"`
Expected: `ok`

- [ ] **Step 9: Run full test suite**

Run: `cd /mnt/data/projects/hive && uv run pytest tests/ -v`
Expected: All tests pass. If any existing tests reference `preview_text`, update them to use `synopsis`.

- [ ] **Step 10: Commit**

```bash
git add src/hive/app.py
git commit -m "feat(synopsis): integrate synopsis generation into dashboard refresh loop"
```

---

### Task 10: Manual smoke test

**Files:** None (verification only)

- [ ] **Step 1: Start the dashboard**

Run: `cd /mnt/data/projects/hive && uv run hive`

- [ ] **Step 2: Verify synopsis appears**

Highlight a session that has an active conversation. The preview pane should show a 2-3 line synopsis instead of raw terminal output. First load may show "(generating synopsis...)" briefly.

- [ ] **Step 3: Verify cache is created**

Run: `ls ~/.claude/hive/synopsis/`
Expected: JSON files for each session that has had its synopsis generated.

- [ ] **Step 4: Verify fallback without credentials**

Temporarily unset API env vars and restart. Sessions should show the first user message as fallback text.

- [ ] **Step 5: Verify kill cleanup**

Kill a session with `k`. Check that `~/.claude/hive/synopsis/{session_name}.json` is deleted.

- [ ] **Step 6: Final commit if any fixups needed**

```bash
git add -A
git commit -m "fix(synopsis): post-smoke-test fixups"
```
