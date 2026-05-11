import asyncio
import json
import pytest
from unittest.mock import patch, MagicMock

from hive.synopsis import (
    extract_conversation,
    build_client,
    load_cached_synopsis,
    save_cached_synopsis,
)


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def cache_dir(fake_home):
    d = fake_home / ".claude" / "hive" / "synopsis"
    d.mkdir(parents=True, exist_ok=True)
    return d


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


def test_get_synopsis_returns_cached(fake_home, cache_dir, monkeypatch):
    from hive.synopsis import get_synopsis
    monkeypatch.setattr("hive.synopsis._client_cache", None)
    jsonl_path = _make_jsonl(fake_home, "/home/user/proj", "sess-1", [
        ("user", "Hello"),
        ("assistant", "Hi"),
    ])
    mtime = jsonl_path.stat().st_mtime
    size = jsonl_path.stat().st_size
    save_cached_synopsis("my-session", "sess-1", mtime, size, "Cached synopsis.")

    result = asyncio.run(get_synopsis("my-session", "/home/user/proj", "sess-1"))
    assert result == "Cached synopsis."


def test_get_synopsis_generates_when_no_cache(fake_home, cache_dir, monkeypatch):
    from hive.synopsis import get_synopsis
    monkeypatch.setattr("hive.synopsis._client_cache", None)
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


def test_get_synopsis_no_credentials_fallback(fake_home, cache_dir, monkeypatch):
    from hive.synopsis import get_synopsis
    monkeypatch.setattr("hive.synopsis._client_cache", None)
    _make_jsonl(fake_home, "/home/user/proj", "sess-1", [
        ("user", "Help me refactor the database layer"),
    ])
    with patch("hive.synopsis.build_client", return_value=(None, None)):
        result = asyncio.run(get_synopsis("my-session", "/home/user/proj", "sess-1"))
    assert "refactor the database layer" in result


def test_get_synopsis_no_jsonl(fake_home, cache_dir, monkeypatch):
    from hive.synopsis import get_synopsis
    monkeypatch.setattr("hive.synopsis._client_cache", None)
    result = asyncio.run(get_synopsis("my-session", "/nonexistent", "no-sess"))
    assert result == "(no conversation yet)"


def test_get_synopsis_api_error_keeps_old_cache(fake_home, cache_dir, monkeypatch):
    from hive.synopsis import get_synopsis
    monkeypatch.setattr("hive.synopsis._client_cache", None)
    jsonl_path = _make_jsonl(fake_home, "/home/user/proj", "sess-1", [
        ("user", "Hello"),
    ])
    mtime1 = jsonl_path.stat().st_mtime
    size1 = jsonl_path.stat().st_size
    save_cached_synopsis("my-session", "sess-1", mtime1, size1, "Old cached synopsis.")

    with open(jsonl_path, "a") as f:
        f.write("\n" + json.dumps({"type": "user", "message": {"content": [{"type": "text", "text": "new msg"}]}}))

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = Exception("API error")

    with patch("hive.synopsis.build_client", return_value=(mock_client, "haiku")):
        result = asyncio.run(get_synopsis("my-session", "/home/user/proj", "sess-1"))
    assert result == "Old cached synopsis."
