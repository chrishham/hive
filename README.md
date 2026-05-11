# hive

A tmux-based TUI orchestrator for multiple parallel Claude Code sessions.

## Install

```bash
uv sync
uv tool install --editable .
```

This installs two console scripts:
- `hive` — launches the dashboard inside a `hive` tmux session
- `hive-hook` — invoked by Claude Code on session events (registered automatically)

## Run

```bash
hive
```

Inside the dashboard:

| Key | Action |
|-----|--------|
| `n` | New session in a tracked project |
| `f` | Free session in `~` |
| `g` | Clone a git repo into a new session |
| `k` | Kill the highlighted session |
| `r` | Resume the highlighted session |
| `R` | Rename the highlighted session |
| `u` | Open detected localhost URL |
| `Enter` | Attach to the highlighted session's tmux window |
| `Q` | Quit (kills all sessions) |

`Ctrl+B 0` from any session window jumps back to the dashboard.

## Session state detection

hive shows each session as **WORKING**, **WAITING**, or **BOOTSTRAPPING**. State is driven primarily by Claude Code hooks (event-based, reliable):

- `UserPromptSubmit` → working
- `Stop`, `SubagentStop`, `Notification` → waiting

The hooks are installed automatically into `~/.claude/settings.json` on startup. To install or reinstall manually:

```bash
hive install-hooks
```

To remove all hive hooks (e.g. before uninstalling):

```bash
hive uninstall-hooks
```

The hook is idempotent and harmless for non-hive Claude Code sessions: it only writes state when the launching tmux window has `HIVE_SESSION=<name>` set.

If hooks are unavailable (e.g. session launched outside hive), hive falls back to scraping the pane text.

State files live at `~/.claude/hive/state/<session-name>.json`.

## Configuration

Config file: `~/.config/hive/config.toml` (auto-generated on first run).

Key options:
- `tmux_session_name` — default `hive`
- `refresh_interval_ms` — dashboard poll rate
- `scan_paths` — directories searched by `n` (new session)

## Tests

```bash
uv run pytest
```
