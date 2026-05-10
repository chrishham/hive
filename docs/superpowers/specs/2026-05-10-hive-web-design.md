# Hive Web — Design

**Date:** 2026-05-10
**Status:** Approved (pending user review of this document)
**Replaces:** the Textual + tmux implementation in `src/hive/`

## Goals

1. **Reliable working/waiting detection.** State must come from a source Claude Code itself writes, not from regex against a TUI buffer.
2. **No tmux.** No window indices, no `select-window`, no fragile session restoration through tmux state.
3. **Browser-based multiplexing.** One tab gives the user a sidebar of all sessions and an interactive terminal for the selected session.
4. **Preserve the existing Claude Code feature set.** Skills, MCP servers, hooks, slash commands, permissions modes — all continue to work because we still spawn `claude` processes; we just give them a PTY we own.

## Non-goals

- Multi-user / authenticated access. v1 binds to `127.0.0.1` only. A token / reverse-proxy story can come later.
- Replacing Claude Code itself with the Agent SDK. Migration cost outweighs benefit while skills/MCP/hooks remain a Claude Code feature.
- Mobile or tablet UI polish. Desktop browser only for v1.
- A full chat-style rendering of conversation history. The terminal IS the conversation view; the JSONL is the state-truth source. Two views of the same data is out of scope.

## Constraints discovered during brainstorming

- **JSONL is flushed in real time.** Empirical test on 2026-05-10: a hive JSONL grew 12 KB in 2 s while the assistant was generating. Every conversation event (`user`, `assistant`, `tool_use`, `tool_result`) is appended as it occurs.
- **JSONL contains non-conversation entries.** Types observed include `permission-mode`, `last-prompt`, `custom-title`, `system`, `attachment`, `file-history-snapshot`, `agent-name`. State derivation must filter to `user` / `assistant` only.
- **Claude Code stream-json is one-shot only.** Interactive bidirectional structured I/O requires the Agent SDK, which we are explicitly not using. Hence the PTY approach.
- **JSONL files don't carry the `--name` we passed at spawn.** Binding a spawned session to its JSONL is a discovery problem (see "Session ↔ JSONL binding" below).

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Browser (single page, vanilla JS + xterm.js)                   │
│                                                                 │
│  ┌──────────────────┐  ┌─────────────────────────────────────┐  │
│  │ Sidebar          │  │ Active session terminal (xterm.js)  │  │
│  │ ● PA-001         │  │                                     │  │
│  │ ◐ apollon WORKING│  │   Claude Code TUI rendered live     │  │
│  │ ● hive           │  │                                     │  │
│  │ [+ new session]  │  │                                     │  │
│  └──────────────────┘  └─────────────────────────────────────┘  │
│                                                                 │
│  WS /ws/state          — state events for sidebar               │
│  WS /ws/pty/<id>       — terminal I/O for active session        │
│  HTTP /sessions        — REST: list / create / delete           │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  FastAPI server (uvicorn, single process, asyncio)              │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌─────────────────────┐    │
│  │ SessionMgr   │  │ StateWatcher │  │ PtyBridge           │    │
│  │ spawn / kill │  │ JSONL→state  │  │ socket↔pty bytes    │    │
│  └──────┬───────┘  └──────┬───────┘  └────────┬────────────┘    │
└─────────┼─────────────────┼───────────────────┼─────────────────┘
          ▼                 ▼                   ▼
   subprocess.Popen    ~/.claude/projects/   per-session
   (in PTY,            *.jsonl               PTY master fd
    process group)     (watchdog inotify)
```

Three server modules, single responsibility each:

### SessionManager

Owns the lifecycle of `claude` subprocesses. Holds the in-memory registry of active sessions. Persists names + project paths + last claude session id to `~/.config/hive/state.json` for recovery across server restarts.

API (internal):
- `spawn(project_path: Path, name: str) -> Session`
- `kill(session_id: str) -> None`
- `list() -> list[Session]`
- `get(session_id: str) -> Session | None`
- `restart_persisted() -> None` (called once at server startup)

### StateWatcher

Watches `~/.claude/projects/` recursively with `watchdog`. Maintains a debounced read of each tracked JSONL's tail. On state change, publishes `(session_id, state)` to an internal asyncio.Queue consumed by the WebSocket fan-out for `/ws/state`.

Pure file → state derivation. Knows nothing about subprocesses or PTYs. Receives the set of `(session_id, jsonl_path)` bindings from SessionManager.

API (internal):
- `bind(session_id: str, jsonl_path: Path) -> None`
- `unbind(session_id: str) -> None`
- `subscribe() -> AsyncIterator[StateEvent]`

### PtyBridge

Pumps bytes between a WebSocket connection and a PTY master fd. One pair of asyncio tasks per connected viewer (multiple viewers per session are allowed; bytes from the PTY fan out to each).

API (internal):
- `attach(session_id: str, websocket: WebSocket) -> None` (runs until disconnect)

## Data model

```python
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

class SessionState(Enum):
    BOOTSTRAPPING = "bootstrapping"  # spawned, JSONL not yet written or no turns
    WORKING = "working"              # last conversation event implies generating/tool
    WAITING = "waiting"              # last event is end_turn, or stale
    DEAD = "dead"                    # process exited

@dataclass
class Session:
    id: str                       # uuid we generate at spawn time
    name: str                     # user-facing, e.g. "apollon-hotel-001"
    project_path: Path
    pid: int | None               # claude process pid, None if dead
    pty_master_fd: int | None     # closed when session dies
    jsonl_path: Path | None       # discovered shortly after spawn
    claude_session_id: str | None # parsed from JSONL header for --resume
    created_at: datetime
    state: SessionState
```

Persisted to `~/.config/hive/state.json`:

```json
{
  "sessions": [
    {
      "id": "9b8e...",
      "name": "apollon-hotel-001",
      "project_path": "/mnt/data/projects/apollon-hotel",
      "claude_session_id": "ee8aa5ca-aa4f-4801-9618-79a4e97e7f64"
    }
  ]
}
```

`pid`, `pty_master_fd`, `state`, and `jsonl_path` are all derived at startup — they aren't persisted because they don't survive a process restart.

## State derivation

```python
CONVERSATION_TYPES = {"user", "assistant"}
STALE_AFTER = timedelta(minutes=5)

def derive_state(jsonl_path: Path, now: datetime) -> SessionState:
    last = read_last_conversation_event(jsonl_path)
    if last is None:
        return SessionState.BOOTSTRAPPING

    age = now - parse_iso8601(last["timestamp"])
    if age > STALE_AFTER:
        return SessionState.WAITING

    msg = last.get("message", {})
    stop = msg.get("stop_reason")
    if last["type"] == "assistant":
        if stop == "end_turn":
            return SessionState.WAITING
        return SessionState.WORKING  # tool_use, max_tokens, mid-stream
    # type == "user": fresh input or tool_result, assistant is generating
    return SessionState.WORKING
```

`read_last_conversation_event` reads the file from the end backward in chunks (e.g., 8 KB at a time), splits into lines, parses each as JSON, returns the last one whose `type` is in `CONVERSATION_TYPES`. This avoids loading 1+ MB transcripts on every state check.

## Session ↔ JSONL binding

At spawn time we know `(project_path, spawn_timestamp)` but not the JSONL uuid Claude Code will choose. Binding strategy:

1. Compute `encoded = project_path.replace("/", "-")`.
2. Watch `~/.claude/projects/<encoded>/` (create the dir if missing) with `watchdog`.
3. The first new `*.jsonl` file created with mtime `>=` spawn_timestamp belongs to this session. Record its path on the Session object and call `StateWatcher.bind`.
4. As a sanity check, parse the first event in that JSONL and confirm its timestamp is within ~10 s of spawn. If not, fall back to scanning all JSONLs in the dir and picking the one whose first event is closest to spawn.
5. Derive `claude_session_id` from the JSONL filename itself (it is the `<uuid>` part of `<uuid>.jsonl`). Store it on the Session and persist to `state.json` so a server restart can `claude --resume <claude_session_id>` to continue the same conversation.

Failure mode: if no JSONL appears within 30 s of spawn, log an error and mark the session `DEAD`. The user can retry from the UI.

## Session lifecycle

### Spawn

```
POST /sessions  {project_path, name}
  ├─ SessionManager.spawn():
  │    pty_master, pty_slave = pty.openpty()
  │    proc = subprocess.Popen(
  │       ["claude", "--dangerously-skip-permissions", "--name", name],
  │       cwd=project_path,
  │       stdin=pty_slave, stdout=pty_slave, stderr=pty_slave,
  │       start_new_session=True,
  │       env={**os.environ, "TERM": "xterm-256color"})
  │    os.close(pty_slave)  # parent doesn't need slave end
  │    register Session(pid=proc.pid, pty_master_fd=pty_master, ...)
  ├─ StateWatcher binds the JSONL when it appears (within ~1 s typically).
  └─ Broadcast on /ws/state → sidebar updates.
```

### View / interact

```
Browser opens WebSocket /ws/pty/<id>
  PtyBridge.attach():
    Task A:  socket.recv() → os.write(pty_master_fd, bytes)
    Task B:  os.read(pty_master_fd, 4096) → socket.send(bytes) + scrollback buffer
  When socket disconnects, both tasks exit; PTY stays open.
```

Multiple viewers per session are allowed: PtyBridge keeps a set of attached websockets per session and fans out PTY-output bytes to all.

### Kill

```
DELETE /sessions/<id>
  ├─ SessionManager.kill():
  │    os.killpg(os.getpgid(pid), SIGTERM)
  │    wait 2 s; if still alive, SIGKILL
  │    os.close(pty_master_fd)
  │    StateWatcher.unbind()
  └─ Remove from state.json. JSONL on disk is preserved as history.
```

### Server restart

```
On startup:
  ├─ Read state.json
  ├─ For each persisted session:
  │    if claude_session_id is known:
  │      spawn with ["claude", ..., "--name", name, "--resume", id]
  │    else:
  │      spawn with ["claude", ..., "--name", name, "--continue"]
  ├─ Re-bind JSONLs (existing files; the binding heuristic still works
  │    because the new claude process appends to the resumed file).
  └─ Resume normal operation.
```

### Process crash (claude exits on its own)

```
SIGCHLD handler / asyncio.subprocess wait:
  ├─ Mark Session.state = DEAD, pid = None, close pty_master_fd
  ├─ Broadcast state change
  └─ Sidebar shows the session greyed out with a "restart" button.
```

## Scrollback buffer

Each session keeps a circular byte buffer of its last 256 KB of PTY output. On viewer attach, the buffer is sent first, then live bytes follow. Memory cost: 256 KB × N sessions (~1.5 MB at 6 sessions). Hugely improves UX on browser refresh.

The buffer is server-side only and is wiped on session death or server restart. JSONL remains the durable history.

## Tech stack

| Layer | Choice | Reason |
|---|---|---|
| Server | Python 3.11+, FastAPI, uvicorn | Matches existing `uv` workflow; FastAPI's WebSocket support is first-class. |
| File watching | `watchdog` | Cross-platform inotify wrapper, already a hive dependency-eligible choice. |
| PTY | stdlib `pty` + `os` + `subprocess.Popen` | No third-party PTY lib needed; `pty.openpty()` plus `start_new_session=True` is sufficient. |
| Frontend | Vanilla JS + xterm.js (CDN or vendored) | No build step; aligned with "no React" decision. |
| Frontend bundling | None — single `index.html` + a few `.js` modules served statically. | Keeps the deploy story `uv run hive-web` and that's it. |

Dependencies added to `pyproject.toml`: `fastapi`, `uvicorn[standard]`, `watchdog`. WebSocket support comes via Starlette (bundled with FastAPI) on the server side and via the browser's native `WebSocket` on the client side; no separate `websockets` library needed.

## Error handling

| Failure | Detection | Response |
|---|---|---|
| `claude` binary not found at spawn | `FileNotFoundError` from Popen | 400 to the POST `/sessions`, surfaced in UI |
| JSONL never appears after spawn | 30 s timeout in binding logic | Session → DEAD, log error, UI shows failure |
| `claude` process crashes mid-session | SIGCHLD / `proc.poll()` | Session → DEAD, scrollback buffer flushed to viewers, sidebar updates |
| WebSocket disconnects | exception in PtyBridge tasks | Tasks exit cleanly; PTY remains; viewer can reattach |
| JSONL file disappears (manual delete) | watchdog DELETE event | Session marked DEAD on next state check; warn in log |
| Disk full, can't write state.json | OSError on save | Log error, keep going in-memory; flagged in /healthz |
| Two browsers spawn the same session name simultaneously | name uniqueness check in SessionManager | Second request gets 409 Conflict |

## Observability

- Standard library `logging`, JSON-formatted lines to stdout.
- Per-session log lines tagged with `session_id` for grep-ability.
- `GET /healthz` returns `{"sessions": N, "state_watcher": "ok"|"degraded", "uptime_s": N}`.
- No metrics endpoint in v1.

## Testing strategy

Unit-testable, one suite per module:

- `tests/test_state_derivation.py` — table-driven: synthetic JSONL contents → expected state. Includes the cases captured during brainstorming (end_turn → WAITING, tool_use without tool_result → WORKING, stale → WAITING, etc.) and edge cases (empty file, only metadata events, malformed lines).
- `tests/test_session_manager.py` — spawn/kill against a mock `claude` script (a Python script that writes a known JSONL pattern); verifies process group cleanup.
- `tests/test_state_watcher.py` — uses `tmp_path` and a fake JSONL written incrementally; asserts state transitions are emitted within ~100 ms.
- `tests/test_pty_bridge.py` — round-trip bytes through a test PTY using `pytest-asyncio`.
- `tests/test_api.py` — FastAPI `TestClient` for the REST endpoints; WebSocket smoke test using `websockets` test fixtures.

End-to-end test deferred (would require launching a real `claude`); v1 ships with the unit suite + a manual smoke-test checklist in the README.

## Migration / coexistence

- Old code (`src/hive/app.py`, `src/hive/tmux.py`, `src/hive/widgets/`, the Textual-based dashboard) moves to `src/hive_legacy/` with no behavioural changes. Two `pyproject.toml` script entries: `hive` (legacy) and `hive-web` (new).
- `src/hive/detector.py`'s pure helpers (`detect_model`, `detect_context_pct_from_pane`, `detect_urls`, `probe_url`) are reused by the new code where applicable; the TUI-parsing `detect_state` is left in legacy and not used by the new server.
- State file location: legacy is `~/.config/hive/state.json` (existing). New uses `~/.config/hive/state.json` as well, with a different schema; legacy state file is renamed to `state.tmux.json` on first run of `hive-web`.
- Once the user is satisfied with `hive-web`, `hive_legacy/` can be deleted in a follow-up commit.

## Open questions resolved during brainstorming

| Question | Resolution |
|---|---|
| TUI parsing or structured output? | Structured: JSONL events + PTY for interaction. |
| Spawn ourselves or watch user-launched processes? | Spawn ourselves so we own the PTY and lifecycle. |
| Agent SDK or Claude Code? | Claude Code, to preserve user's skill/MCP/hook investment. |
| One terminal at a time or split panes? | One at a time (sidebar + main panel). Splits can be a v2 feature. |
| Replay scrollback on attach? | Yes, 256 KB circular buffer per session. |
| Authentication? | None in v1; localhost-only bind. |

## Out of scope (deliberately)

- Authentication / multi-user
- Split-pane / grid layout
- Persistent server-side scrollback across restarts
- Mobile UI
- Replacing skills/MCP/hooks with web-native equivalents
- Sharing sessions across machines
- Recording / replay of sessions for later review (the JSONL already serves as history if needed)
