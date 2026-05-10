# Hive

Browser multiplexer for Claude Code sessions. Replaces tmux.

## Quick start

```bash
uv sync
uv run hive
# open http://127.0.0.1:8765
```

## How it works

- Hive spawns each `claude` session in a PTY it owns; no tmux.
- The browser shows a sidebar of sessions and the active session's TUI in an `xterm.js` panel.
- Session state (working/waiting) comes from Claude Code's own JSONL transcripts in `~/.claude/projects/`, not from screen scraping. Reliable.

## CLI flags

- `--host` — bind address (default `127.0.0.1`)
- `--port` — bind port (default `8765`)
- `--state-path` — where session metadata is persisted (default `~/.config/hive/state.json`)
- `--claude-projects-dir` — Claude Code transcripts root (default `~/.claude/projects`)
- `--claude-cmd` — claude binary name or path (default `claude`)

## Legacy

The pre-2026-05 Textual + tmux dashboard still ships as `hive-legacy`:

```bash
uv run hive-legacy
```

## Tests

```bash
uv run pytest
```

## Manual smoke test

After `uv run hive`, in the browser:

1. Click `+`, set project path to a real Claude Code project, give it a name.
2. The new row appears in the sidebar and flips through `bootstrapping → waiting` (or `working` if the session is mid-turn).
3. Click the row — the session's terminal appears in the main panel.
4. Type a prompt that takes >5 s (e.g., "count to 30 slowly").
5. While Claude works, the row's icon flips to `◐` (blue) `working`. After it finishes, back to `●` (yellow) `waiting`.
6. Click `✕` to kill the session.
