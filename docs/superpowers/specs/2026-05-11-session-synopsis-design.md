# Session Synopsis Design

Replace the raw tmux pane preview in the Hive dashboard with an LLM-generated session synopsis, cached to disk and refreshed when the conversation changes.

## Context

The dashboard preview pane currently shows the last ~20 lines of cleaned raw terminal output from each session's tmux pane. This is noisy, hard to parse, and doesn't convey what the session is actually about. A short natural-language synopsis would be far more useful.

Claude Code stores full conversation history as JSONL files at `~/.claude/projects/{encoded-path}/{session_id}.jsonl`. The session's `session_id` is available from the hook state file at `~/.claude/hive/state/{session_name}.json`. Combined with the `project_path` from `~/.config/hive/state.json`, this gives us everything needed to locate and read the conversation.

## Design

### Synopsis generation

A new module `src/hive/synopsis.py` handles conversation extraction and LLM summarization.

**Conversation extraction:** Read the session's JSONL file and extract user and assistant text messages. To keep the LLM input small and cheap:
- Include the first user message (establishes the topic).
- Include the last ~10 user/assistant exchanges (shows current state and recent progress).
- Skip system messages, tool calls, attachments, hooks, and other non-conversational entries.
- Cap total extracted text at ~4000 characters to keep Haiku calls fast and cheap.

**LLM summarization:** Send extracted messages to Haiku with a system prompt like:

> "Summarize this Claude Code session in 2-3 concise lines. State what the session is about, what has been accomplished, and what state it's in (e.g., waiting for input, actively working, debugging). Be specific about the actual work, not generic."

The response becomes the synopsis text.

### Provider detection

The synopsis module auto-detects which Claude API provider to use by reading the same environment variables Claude Code uses. A factory function `_build_client()` returns the appropriate Anthropic SDK client:

1. `CLAUDE_CODE_USE_VERTEX=1` — use `anthropic.AnthropicVertex` with `ANTHROPIC_VERTEX_PROJECT_ID` and `CLOUD_ML_REGION` (defaulting to `us-east5`). Model: `claude-haiku-4-5@20251001`.
2. `ANTHROPIC_FOUNDRY_API_KEY` set — use `anthropic.AnthropicFoundry` with the foundry resource/base URL. Model: `claude-haiku-4-5`.
3. `ANTHROPIC_API_KEY` set — use `anthropic.Anthropic`. Model: `claude-haiku-4-5-20251001`.
4. None available — synopsis generation is disabled; preview pane shows a fallback message extracted from the JSONL (first user message, similar to what `dialogs.py` does today).

The client is instantiated once and reused across synopsis calls.

### Caching

Synopses are cached to `~/.claude/hive/synopsis/{session_name}.json`:

```json
{
  "session_id": "3529f5af-...",
  "jsonl_mtime": 1715446890.123,
  "jsonl_size": 284510,
  "synopsis": "Working on replacing the dashboard preview pane with LLM-generated session synopses. Implemented the synopsis module with provider auto-detection and caching. Currently debugging the preview pane integration.",
  "generated_at": "2026-05-11T18:30:00+00:00"
}
```

Cache invalidation: compare the JSONL file's `mtime` and `size` against the cached values. If either changed, regenerate.

### Integration with the dashboard

**`SessionData`** (`session_list.py`): Replace the `preview_text: str` field with `synopsis: str`. Remove all raw pane text storage for preview purposes. The pane text is still captured for state detection, model detection, URL scanning, and context percentage — it's just no longer passed to the preview pane.

**`PreviewPane`** (`preview.py`): Simplify `set_content()` to display the synopsis string directly. Remove `clean_preview()` and the ANSI/box-drawing cleanup code (no longer needed). Show `"(generating synopsis...)"` while waiting for the first generation, and `"(no conversation yet)"` if the JSONL doesn't exist or has no messages.

**`_refresh_sessions()`** (`app.py`): After building each session's data, call the synopsis module to get the cached or freshly-generated synopsis. Synopsis generation runs asynchronously so it doesn't block the refresh loop. If generation is in-flight, display the previous cached synopsis (or the placeholder).

**`_update_preview()`** (`app.py`): Pass `data.synopsis` instead of `data.preview_text`.

### Async synopsis generation

Synopsis generation involves file I/O and an API call, so it must not block the UI. Implementation:

- Use `asyncio.to_thread()` to run the synchronous Anthropic SDK call off the main thread.
- Track in-flight generation per session to avoid duplicate concurrent calls.
- On generation failure (network error, auth error), log the error and keep the previous cached synopsis. Don't crash or show error text in the preview pane.

### Cleanup

When a session is killed, delete its synopsis cache file alongside the existing hook state cleanup.

## Dependencies

Add `anthropic` to `pyproject.toml` dependencies. The SDK includes `AnthropicVertex` and `AnthropicFoundry` client classes — no additional packages needed for provider support. (Vertex auth uses Application Default Credentials via `google-auth`, which should already be available if `CLAUDE_CODE_USE_VERTEX=1` is set.)

## Files changed

| File | Change |
|------|--------|
| `src/hive/synopsis.py` | New module: conversation extraction, LLM call, caching, provider detection |
| `src/hive/widgets/preview.py` | Simplify to display synopsis text; remove `clean_preview()` |
| `src/hive/widgets/session_list.py` | Replace `preview_text` field with `synopsis` in `SessionData` |
| `src/hive/app.py` | Integrate synopsis into refresh loop; update `_update_preview()` |
| `pyproject.toml` | Add `anthropic` dependency |

## Fallback behavior

If no API credentials are available, the synopsis module falls back to extracting the first user message from the JSONL (truncated to ~200 chars) — the same approach `dialogs.py` uses today but with more text. This ensures the preview pane always shows something meaningful, even without LLM access.

## Error handling

- JSONL file not found or unreadable: show `"(no conversation yet)"`
- API call fails: log warning, keep previous cached synopsis
- Malformed JSONL entries: skip silently (already handled in `dialogs.py` pattern)
- Cache file corrupt: regenerate on next refresh

## Testing

- Unit test `synopsis.py`: conversation extraction from sample JSONL data
- Unit test cache invalidation logic (mtime/size comparison)
- Unit test provider detection from env vars
- Integration test with mocked Anthropic client
