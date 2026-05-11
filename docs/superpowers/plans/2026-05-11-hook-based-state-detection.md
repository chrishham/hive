# Hook-Based Session State Detection — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace fragile pane-text scraping with event-driven state detection. Each hive-launched tmux window gets a `HIVE_SESSION=<name>` env var; a globally-installed Claude Code hook script writes session state to `~/.claude/hive/state/<name>.json` on each event. The dashboard reads this file (with pane-scraping retained as fallback for sessions without the hook).

**Architecture:**
- A single console script `hive-hook` is invoked by Claude Code on `UserPromptSubmit`, `Stop`, `SubagentStop`, `Notification`, `SessionStart`, and `SessionEnd` events.
- The hook reads JSON from stdin, looks up `HIVE_SESSION` in its env, maps the event to a `SessionState`, and atomically writes the state file. If `HIVE_SESSION` is unset (sessions launched outside hive), it exits 0 — never breaking unrelated Claude Code sessions.
- `HiveApp._refresh_sessions` consults `read_session_state(name)` first; on `None` it falls back to `detect_state(pane_text)`.
- Hooks are installed idempotently into `~/.claude/settings.json` on app start and via `hive install-hooks` CLI command.

**Tech Stack:** Python 3.11+, Textual, Claude Code hooks JSON protocol, `tmux new-window -e`, `pytest`, `uv`.

---

## File Structure

**Create:**
- `src/hive/hook_state.py` — reader API: `read_session_state(name)`, `remove_session_state(name)`, `STATE_DIR` constant
- `src/hive/hook_writer.py` — `main()` entry point invoked as `hive-hook`
- `src/hive/install_hooks.py` — `install_hooks()` library function; safe/idempotent
- `tests/test_hook_state.py`
- `tests/test_hook_writer.py`
- `tests/test_install_hooks.py`
- `README.md`

**Modify:**
- `pyproject.toml` — register `hive-hook` console script
- `src/hive/tmux.py` — `new_window()` accepts `env: dict[str, str] | None`
- `src/hive/app.py` — pass `env={"HIVE_SESSION": name}` on session create/restore; consult hook state in `_refresh_sessions`; remove state file in `action_kill_session`; auto-install hooks in `__init__`
- `src/hive/__main__.py` — add `hive install-hooks` subcommand
- `tests/test_tmux.py` — cover env-var path

---

## Task 1: State file reader

**Files:**
- Create: `src/hive/hook_state.py`
- Test: `tests/test_hook_state.py`

State file shape (one JSON object per session):
```json
{
  "state": "working",
  "session_id": "uuid-from-claude-code",
  "updated_at": "2026-05-11T12:34:56+00:00",
  "event": "UserPromptSubmit"
}
```

Only `state` is required; reader tolerates missing optional fields and unknown values.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_hook_state.py
import json
import pytest

from hive.detector import SessionState
from hive.hook_state import read_session_state, remove_session_state, state_file_path


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def _write_state(home, name, payload):
    path = home / ".claude" / "hive" / "state" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    return path


def test_missing_file_returns_none(fake_home):
    assert read_session_state("nope") is None


def test_reads_working(fake_home):
    _write_state(fake_home, "foo", {"state": "working"})
    assert read_session_state("foo") == SessionState.WORKING


def test_reads_waiting(fake_home):
    _write_state(fake_home, "foo", {"state": "waiting"})
    assert read_session_state("foo") == SessionState.WAITING


def test_corrupt_json_returns_none(fake_home):
    path = fake_home / ".claude" / "hive" / "state" / "foo.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json{")
    assert read_session_state("foo") is None


def test_unknown_state_returns_none(fake_home):
    _write_state(fake_home, "foo", {"state": "exploding"})
    assert read_session_state("foo") is None


def test_remove_session_state_idempotent(fake_home):
    _write_state(fake_home, "foo", {"state": "waiting"})
    remove_session_state("foo")
    remove_session_state("foo")  # second call must not raise
    assert read_session_state("foo") is None


def test_state_file_path_uses_home(fake_home):
    assert state_file_path("bar") == fake_home / ".claude" / "hive" / "state" / "bar.json"
```

- [ ] **Step 2: Run tests to confirm failure**

```bash
uv run pytest tests/test_hook_state.py -v
```
Expected: ImportError (module not found).

- [ ] **Step 3: Implement reader**

```python
# src/hive/hook_state.py
from __future__ import annotations

import json
from pathlib import Path

from hive.detector import SessionState

_STATE_VALUES = {s.value: s for s in SessionState}


def state_dir() -> Path:
    return Path.home() / ".claude" / "hive" / "state"


def state_file_path(name: str) -> Path:
    return state_dir() / f"{name}.json"


def read_session_state(name: str) -> SessionState | None:
    path = state_file_path(name)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    raw = data.get("state") if isinstance(data, dict) else None
    if not isinstance(raw, str):
        return None
    return _STATE_VALUES.get(raw)


def remove_session_state(name: str) -> None:
    state_file_path(name).unlink(missing_ok=True)
```

- [ ] **Step 4: Run tests to confirm pass**

```bash
uv run pytest tests/test_hook_state.py -v
```
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/hive/hook_state.py tests/test_hook_state.py
git commit -m "feat: add hook state file reader"
```

---

## Task 2: Hook writer entry-point

**Files:**
- Create: `src/hive/hook_writer.py`
- Test: `tests/test_hook_writer.py`

Event mapping:
- `UserPromptSubmit` → `working`
- `Stop`, `SubagentStop` → `waiting`
- `SessionStart` → `waiting`
- `Notification` → `waiting` (Claude needs user input)
- `SessionEnd` → delete state file

Behavior contract:
- If `HIVE_SESSION` env var is unset → exit 0 silently.
- If stdin is not valid JSON → exit 0 silently.
- Never write to stdout (could be interpreted as control directive).
- Atomic write: write `<file>.tmp` then `os.replace`.
- Always exit 0 (exit 2 would block Claude).

- [ ] **Step 1: Write failing tests**

```python
# tests/test_hook_writer.py
import json
import subprocess
import sys

import pytest


def _run(payload: dict, env: dict, cwd):
    return subprocess.run(
        [sys.executable, "-m", "hive.hook_writer"],
        input=json.dumps(payload).encode(),
        env=env,
        cwd=cwd,
        capture_output=True,
        timeout=5,
    )


@pytest.fixture
def hook_env(tmp_path, monkeypatch):
    return {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin", "PYTHONPATH": "src"}


def _state_file(home, name):
    from pathlib import Path
    return Path(home) / ".claude" / "hive" / "state" / f"{name}.json"


def test_no_hive_session_exits_zero(tmp_path, hook_env):
    env = {**hook_env}
    env.pop("HIVE_SESSION", None)
    result = _run({"hook_event_name": "Stop", "session_id": "x"}, env, "/data/Projects/hive")
    assert result.returncode == 0
    assert not _state_file(tmp_path, "anything").exists()


def test_user_prompt_submit_writes_working(tmp_path, hook_env):
    env = {**hook_env, "HIVE_SESSION": "foo"}
    result = _run(
        {"hook_event_name": "UserPromptSubmit", "session_id": "abc"},
        env,
        "/data/Projects/hive",
    )
    assert result.returncode == 0
    data = json.loads(_state_file(tmp_path, "foo").read_text())
    assert data["state"] == "working"
    assert data["session_id"] == "abc"
    assert data["event"] == "UserPromptSubmit"
    assert "updated_at" in data


def test_stop_writes_waiting(tmp_path, hook_env):
    env = {**hook_env, "HIVE_SESSION": "foo"}
    _run({"hook_event_name": "Stop", "session_id": "abc"}, env, "/data/Projects/hive")
    data = json.loads(_state_file(tmp_path, "foo").read_text())
    assert data["state"] == "waiting"


def test_session_end_deletes_file(tmp_path, hook_env):
    env = {**hook_env, "HIVE_SESSION": "foo"}
    _run({"hook_event_name": "Stop", "session_id": "abc"}, env, "/data/Projects/hive")
    assert _state_file(tmp_path, "foo").exists()
    _run({"hook_event_name": "SessionEnd", "session_id": "abc"}, env, "/data/Projects/hive")
    assert not _state_file(tmp_path, "foo").exists()


def test_invalid_json_exits_zero(tmp_path, hook_env):
    env = {**hook_env, "HIVE_SESSION": "foo"}
    result = subprocess.run(
        [sys.executable, "-m", "hive.hook_writer"],
        input=b"not json",
        env=env,
        cwd="/data/Projects/hive",
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == 0
    assert not _state_file(tmp_path, "foo").exists()


def test_unknown_event_is_noop(tmp_path, hook_env):
    env = {**hook_env, "HIVE_SESSION": "foo"}
    _run({"hook_event_name": "Mystery"}, env, "/data/Projects/hive")
    assert not _state_file(tmp_path, "foo").exists()
```

- [ ] **Step 2: Run tests to confirm failure**

```bash
uv run pytest tests/test_hook_writer.py -v
```
Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement writer**

```python
# src/hive/hook_writer.py
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

from hive.hook_state import remove_session_state, state_dir, state_file_path

EVENT_TO_STATE = {
    "UserPromptSubmit": "working",
    "Stop": "waiting",
    "SubagentStop": "waiting",
    "Notification": "waiting",
    "SessionStart": "waiting",
}


def main() -> int:
    session_name = os.environ.get("HIVE_SESSION")
    if not session_name:
        return 0

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(payload, dict):
        return 0

    event = payload.get("hook_event_name", "")

    if event == "SessionEnd":
        remove_session_state(session_name)
        return 0

    state = EVENT_TO_STATE.get(event)
    if state is None:
        return 0

    record = {
        "state": state,
        "session_id": payload.get("session_id", ""),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "event": event,
    }

    state_dir().mkdir(parents=True, exist_ok=True)
    path = state_file_path(session_name)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record))
    os.replace(tmp, path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to confirm pass**

```bash
uv run pytest tests/test_hook_writer.py -v
```
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/hive/hook_writer.py tests/test_hook_writer.py
git commit -m "feat: add Claude Code hook writer for session state"
```

---

## Task 3: Register `hive-hook` console script

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add console script**

In `pyproject.toml`, replace the `[project.scripts]` block with:

```toml
[project.scripts]
hive = "hive.__main__:main"
hive-hook = "hive.hook_writer:main"
```

- [ ] **Step 2: Reinstall package and confirm script is on PATH**

```bash
uv sync
uv run which hive-hook
```
Expected: a path under `.venv/bin/hive-hook`.

- [ ] **Step 3: Smoke test**

```bash
echo '{"hook_event_name":"Stop","session_id":"smoke"}' | HIVE_SESSION=smoketest uv run hive-hook
cat ~/.claude/hive/state/smoketest.json
rm ~/.claude/hive/state/smoketest.json
```
Expected: file contains `"state": "waiting"`.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "feat: register hive-hook console script"
```

---

## Task 4: Hooks installer

**Files:**
- Create: `src/hive/install_hooks.py`
- Test: `tests/test_install_hooks.py`

Idempotent install into `~/.claude/settings.json`. Adds one entry per event referencing `hive-hook`. If a hive-hook entry already exists for an event, it is skipped. If `settings.json` is corrupt, returns False without overwriting.

Hook event entry shape:
```json
{
  "matcher": "",
  "hooks": [{"type": "command", "command": "hive-hook"}]
}
```

- [ ] **Step 1: Write failing tests**

```python
# tests/test_install_hooks.py
import json

import pytest

from hive.install_hooks import HOOK_COMMAND, HOOK_EVENTS, install_hooks


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def _settings_path(home):
    return home / ".claude" / "settings.json"


def test_install_into_empty(fake_home):
    assert install_hooks() is True
    data = json.loads(_settings_path(fake_home).read_text())
    for event in HOOK_EVENTS:
        entries = data["hooks"][event]
        assert any(
            h.get("command") == HOOK_COMMAND
            for entry in entries
            for h in entry.get("hooks", [])
        )


def test_install_preserves_existing_unrelated(fake_home):
    _settings_path(fake_home).parent.mkdir(parents=True, exist_ok=True)
    _settings_path(fake_home).write_text(json.dumps({
        "model": "opus",
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "audit.sh"}]}
            ]
        }
    }))
    install_hooks()
    data = json.loads(_settings_path(fake_home).read_text())
    assert data["model"] == "opus"
    assert data["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "audit.sh"


def test_install_is_idempotent(fake_home):
    install_hooks()
    install_hooks()
    data = json.loads(_settings_path(fake_home).read_text())
    for event in HOOK_EVENTS:
        commands = [
            h["command"]
            for entry in data["hooks"][event]
            for h in entry.get("hooks", [])
            if h.get("command") == HOOK_COMMAND
        ]
        assert len(commands) == 1


def test_install_refuses_corrupt_settings(fake_home):
    _settings_path(fake_home).parent.mkdir(parents=True, exist_ok=True)
    _settings_path(fake_home).write_text("not json{")
    assert install_hooks() is False
    assert _settings_path(fake_home).read_text() == "not json{"
```

- [ ] **Step 2: Run tests to confirm failure**

```bash
uv run pytest tests/test_install_hooks.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement installer**

```python
# src/hive/install_hooks.py
from __future__ import annotations

import json
from pathlib import Path

HOOK_COMMAND = "hive-hook"
HOOK_EVENTS = (
    "UserPromptSubmit",
    "Stop",
    "SubagentStop",
    "Notification",
    "SessionStart",
    "SessionEnd",
)


def settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def install_hooks() -> bool:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        try:
            settings = json.loads(path.read_text())
        except json.JSONDecodeError:
            return False
        if not isinstance(settings, dict):
            return False
    else:
        settings = {}

    hooks = settings.setdefault("hooks", {})
    for event in HOOK_EVENTS:
        entries = hooks.setdefault(event, [])
        already = any(
            h.get("command") == HOOK_COMMAND
            for entry in entries
            for h in entry.get("hooks", [])
        )
        if not already:
            entries.append({
                "matcher": "",
                "hooks": [{"type": "command", "command": HOOK_COMMAND}],
            })

    path.write_text(json.dumps(settings, indent=2))
    return True
```

- [ ] **Step 4: Run tests to confirm pass**

```bash
uv run pytest tests/test_install_hooks.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/hive/install_hooks.py tests/test_install_hooks.py
git commit -m "feat: idempotent hook installer for ~/.claude/settings.json"
```

---

## Task 5: tmux env-var support

**Files:**
- Modify: `src/hive/tmux.py:59-70`
- Test: `tests/test_tmux.py`

Add `env` parameter to `new_window`. When provided, emit `-e KEY=VALUE` for each pair before the `-P -F` flags. Existing callers without `env` continue to work.

- [ ] **Step 1: Write failing test**

Append to `tests/test_tmux.py`:

```python
def test_new_window_with_env(monkeypatch):
    from hive.tmux import TmuxClient
    captured = {}

    def fake_run(self, args, text=False):
        captured["args"] = args
        class R: returncode = 0; stdout = "5"
        return R()

    monkeypatch.setattr(TmuxClient, "_run", fake_run)
    client = TmuxClient("hive")
    idx = client.new_window("foo", "/tmp", "claude", env={"HIVE_SESSION": "foo"})
    assert idx == 5
    assert "-e" in captured["args"]
    e_idx = captured["args"].index("-e")
    assert captured["args"][e_idx + 1] == "HIVE_SESSION=foo"
```

- [ ] **Step 2: Run test to confirm failure**

```bash
uv run pytest tests/test_tmux.py::test_new_window_with_env -v
```
Expected: TypeError (unexpected keyword `env`).

- [ ] **Step 3: Modify `new_window`**

In `src/hive/tmux.py`, replace the `new_window` method with:

```python
    def new_window(
        self,
        name: str,
        cwd: str,
        command: str,
        env: dict[str, str] | None = None,
    ) -> int:
        args = [
            "tmux", "new-window", "-t", self.session_name,
            "-n", name,
            "-c", cwd,
        ]
        if env:
            for k, v in env.items():
                args.extend(["-e", f"{k}={v}"])
        args.extend(["-P", "-F", "#{window_index}", command])
        result = self._run(args, text=True)
        return int(result.stdout.strip())
```

- [ ] **Step 4: Run all tmux tests**

```bash
uv run pytest tests/test_tmux.py -v
```
Expected: all pass (existing tests still call without `env`).

- [ ] **Step 5: Commit**

```bash
git add src/hive/tmux.py tests/test_tmux.py
git commit -m "feat: tmux new_window accepts env dict"
```

---

## Task 6: Pass `HIVE_SESSION` on session launch

**Files:**
- Modify: `src/hive/app.py` — `_create_session` (~line 279), `_restore_sessions` (~line 75)
- Modify: `src/hive/__main__.py` — `new` subcommand (line 43)

- [ ] **Step 1: Update `_create_session`**

Replace the `new_window` call in `_create_session`:

```python
        window_idx = self.tmux.new_window(
            session_name, project_path, cmd, env={"HIVE_SESSION": session_name}
        )
```

- [ ] **Step 2: Update `_restore_sessions`**

Replace the `new_window` call in the loop:

```python
            window_idx = self.tmux.new_window(
                name, project_path, cmd, env={"HIVE_SESSION": name}
            )
```

- [ ] **Step 3: Update `__main__.py` `new` command**

Replace line 43:

```python
        tmux.new_window(
            name, path,
            f"claude --dangerously-skip-permissions --name {name}",
            env={"HIVE_SESSION": name},
        )
```

- [ ] **Step 4: Manual smoke test**

```bash
uv run pytest -q
uv run hive new /tmp  # creates a window, attach with `tmux attach -t hive`
# In the new window run: echo $HIVE_SESSION   (should print the session name)
```

- [ ] **Step 5: Commit**

```bash
git add src/hive/app.py src/hive/__main__.py
git commit -m "feat: set HIVE_SESSION env var in launched tmux windows"
```

---

## Task 7: Use hook state in refresh loop

**Files:**
- Modify: `src/hive/app.py:104-150` (`_refresh_sessions`)

- [ ] **Step 1: Add import**

At top of `src/hive/app.py`, add:

```python
from hive.hook_state import read_session_state
```

- [ ] **Step 2: Replace state assignment in `_refresh_sessions`**

Replace:

```python
            pane_text = self.tmux.capture_pane(win["index"])
            state = detect_state(pane_text)
```

with:

```python
            pane_text = self.tmux.capture_pane(win["index"])
            hook_state = read_session_state(name)
            state = hook_state if hook_state is not None else detect_state(pane_text)
```

- [ ] **Step 3: Manual verification**

```bash
uv run hive
# Launch a new session via 'n' or 'f'. After hook is installed (Task 9 covers
# auto-install; for now run `uv run python -c "from hive.install_hooks import install_hooks; install_hooks()"`)
# - Submit a prompt → dashboard should show WORKING immediately
# - When Claude finishes → dashboard should show WAITING immediately
```

- [ ] **Step 4: Commit**

```bash
git add src/hive/app.py
git commit -m "feat: prefer hook-driven session state, fall back to pane detection"
```

---

## Task 8: Cleanup state file on session kill

**Files:**
- Modify: `src/hive/app.py:335-344` (`action_kill_session`)

- [ ] **Step 1: Add import**

In `src/hive/app.py`, extend the existing hook_state import:

```python
from hive.hook_state import read_session_state, remove_session_state
```

- [ ] **Step 2: Remove state file on kill**

In `action_kill_session`, after `self.state.save_default()`, add:

```python
            remove_session_state(data.name)
```

- [ ] **Step 3: Manual verification**

```bash
uv run hive
# Kill a session via 'k'. Confirm:
ls ~/.claude/hive/state/   # killed session's file should be gone
```

- [ ] **Step 4: Commit**

```bash
git add src/hive/app.py
git commit -m "feat: clean up hook state file when killing session"
```

---

## Task 9: CLI `install-hooks` + auto-install on app start

**Files:**
- Modify: `src/hive/__main__.py`
- Modify: `src/hive/app.py` (`HiveApp.__init__`)

- [ ] **Step 1: Add `install-hooks` subcommand**

In `src/hive/__main__.py`, after the `if command == "list":` block, add:

```python
    if command == "install-hooks":
        from hive.install_hooks import install_hooks, settings_path
        if install_hooks():
            print(f"Installed hive hooks into {settings_path()}")
            return
        print(f"Could not install hooks (corrupt {settings_path()}?)")
        sys.exit(1)
```

Update the usage line:

```python
        print("Usage: hive [attach|list|new <path>|install-hooks]")
```

- [ ] **Step 2: Auto-install in `HiveApp.__init__`**

In `src/hive/app.py`, at the end of `HiveApp.__init__`, add:

```python
        from hive.install_hooks import install_hooks
        install_hooks()
```

- [ ] **Step 3: Verify**

```bash
uv run hive install-hooks
# Then inspect:
cat ~/.claude/settings.json
# Run again — should be idempotent (no duplicates)
uv run hive install-hooks
```

- [ ] **Step 4: Commit**

```bash
git add src/hive/app.py src/hive/__main__.py
git commit -m "feat: auto-install Claude Code hooks on hive startup"
```

---

## Task 10: README

**Files:**
- Create: `README.md`

- [ ] **Step 1: Write README**

```markdown
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
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add README with install, usage, and hooks overview"
```

---

## Self-Review Notes

- All 10 tasks have file paths, code, commands, expected outputs.
- No "TBD"/"TODO"/"add error handling" placeholders.
- Type/name consistency:
  - `read_session_state`, `remove_session_state`, `state_file_path`, `state_dir`, `settings_path`, `install_hooks`, `HOOK_COMMAND`, `HOOK_EVENTS` defined in Tasks 1/4 and reused unchanged in Tasks 7–9.
  - `new_window` `env` parameter introduced in Task 5 and used in Task 6.
- Spec coverage: pane scraping retained as fallback (Task 7); env-var disambiguation across multiple sessions in same cwd (Tasks 5–6); idempotent install (Task 4) + auto-install (Task 9); cleanup on kill (Task 8); README (Task 10).
- Known limitation acknowledged in design: if Claude crashes mid-turn no `Stop` fires, state stays `working` until next `UserPromptSubmit` or session kill. Pane fallback can't compensate while hook file exists; future enhancement could time-box state freshness.
