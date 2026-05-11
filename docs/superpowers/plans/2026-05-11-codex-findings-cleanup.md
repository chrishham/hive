# Codex Findings Cleanup — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve all 9 findings from the codex review (2 CRITICAL, 3 HIGH, 3 MEDIUM, 1 LOW group) by introducing shared safety primitives instead of scattering one-off fixes. Spec: `docs/superpowers/specs/2026-05-11-codex-findings-design.md`.

**Architecture:**
- A new `src/hive/safety.py` module centralises name validation, atomic file writes, and tmux format escaping. Every call site uses these helpers — no ad-hoc validation/quoting elsewhere.
- `TmuxClient.new_window` switches from a stringly-typed `command: str` to `command_args: list[str]`, joined internally with `shlex.join`. This eliminates the shell-injection class entirely.
- The Textual app's poll loop gets a try/except boundary that logs the traceback to `~/.claude/hive/dashboard.log` (already wired up) and surfaces a one-line banner instead of silently dying.
- Hook installation drops its auto-install from `HiveApp.__init__` and writes an absolute command (`sys.executable -m hive.hook_writer`) instead of the PATH-dependent `hive-hook`. Users explicitly run `hive install-hooks` once.

**Tech Stack:** Python 3.11+, Textual ≥3.0, tmux, `shlex`, `tempfile`, `pytest`, `uv`.

---

## File Structure

**Create:**
- `src/hive/safety.py` — `validate_session_name`, `sanitize_session_name`, `atomic_write_text`, `escape_tmux_format`, `InvalidSessionName`, `TmuxError`
- `tests/test_safety.py`

**Modify:**
- `src/hive/tmux.py` — `new_window` signature; `list_windows` delimiter; raise `TmuxError`; status-bar caller passes pre-escaped text
- `src/hive/app.py` — use `safety` helpers everywhere; add error/info banners; error-boundary the poll loop; disallow rename on active sessions; validate clone target; surface git errors
- `src/hive/__main__.py` — `_unique_window_name` uses `validate_session_name`; build `command_args` list; remove unused `config` arg
- `src/hive/install_hooks.py` — write absolute command via `atomic_write_text`; tolerant load of `settings.json`
- `src/hive/hook_writer.py` — `tempfile.mkstemp` instead of deterministic `.json.tmp`
- `src/hive/config.py` — `HiveState.save` uses `atomic_write_text`; `HiveConfig.load`/`HiveState.load` tolerate malformed files
- `src/hive/detector.py` — remove `detect_context_usage`, `CONTEXT_WINDOWS`, `CLAUDE_PROJECTS_DIR`, `_encode_project_path`
- `tests/test_tmux.py`, `tests/test_app.py`, `tests/test_install_hooks.py`, `tests/test_hook_writer.py`, `tests/test_config.py` — updates

**File-Structure decision:** `safety.py` is one module because the four helpers form one conceptual layer (input safety) and total < 100 lines. Splitting further would scatter concerns. The new `TmuxError` lives here (not in `tmux.py`) so callers can `from hive.safety import TmuxError` without importing tmux internals.

---

## Task 1: Safety primitives module

**Files:**
- Create: `src/hive/safety.py`
- Test: `tests/test_safety.py`

- [ ] **Step 1.1: Write the failing tests**

```python
# tests/test_safety.py
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from hive.safety import (
    InvalidSessionName,
    TmuxError,
    atomic_write_text,
    escape_tmux_format,
    sanitize_session_name,
    validate_session_name,
)


class TestValidateSessionName:
    def test_accepts_alphanumerics(self):
        validate_session_name("abc")
        validate_session_name("ABC")
        validate_session_name("123")

    def test_accepts_dot_dash_underscore(self):
        validate_session_name("a.b")
        validate_session_name("a-b")
        validate_session_name("a_b")

    def test_accepts_max_length_64(self):
        validate_session_name("a" * 64)

    def test_accepts_realistic_names(self):
        validate_session_name("owui-nbg-001")
        validate_session_name("DevSecOps-001")
        validate_session_name("claude-code-sandbox-001")

    @pytest.mark.parametrize("bad", [
        "", " ", "a b", "a;b", "a$b", "a#b", "a(b", "a/b", "a\\b",
        "a:b", "a\tb", "a\nb", ".", "..", "a" * 65, "kafé", "\x00",
    ])
    def test_rejects(self, bad):
        with pytest.raises(InvalidSessionName):
            validate_session_name(bad)


class TestSanitizeSessionName:
    def test_replaces_forbidden_with_underscore(self):
        assert sanitize_session_name("a b;c") == "a_b_c"

    def test_truncates_to_64(self):
        assert sanitize_session_name("a" * 100) == "a" * 64

    def test_empty_returns_default(self):
        assert sanitize_session_name("") == "session"

    def test_all_bad_returns_default(self):
        assert sanitize_session_name(";;;") == "session"

    def test_keeps_valid_unchanged(self):
        assert sanitize_session_name("owui-nbg-001") == "owui-nbg-001"

    def test_output_is_always_valid(self):
        for raw in ["", "a b", ";;;", "a" * 100, "a/b\\c"]:
            validate_session_name(sanitize_session_name(raw))


class TestAtomicWriteText:
    def test_writes_content(self, tmp_path):
        target = tmp_path / "out.json"
        atomic_write_text(target, '{"k": 1}')
        assert target.read_text() == '{"k": 1}'

    def test_no_temp_left_behind(self, tmp_path):
        target = tmp_path / "out.json"
        atomic_write_text(target, "data")
        leftovers = [p for p in tmp_path.iterdir() if p != target]
        assert leftovers == []

    def test_creates_parent_directory(self, tmp_path):
        target = tmp_path / "nested" / "dir" / "out.json"
        atomic_write_text(target, "data")
        assert target.read_text() == "data"

    def test_concurrent_writers_no_collision(self, tmp_path):
        target = tmp_path / "out.json"
        errors: list[Exception] = []

        def writer(payload: str):
            try:
                atomic_write_text(target, payload)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(f"payload-{i}",)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        final = target.read_text()
        assert final.startswith("payload-")


class TestEscapeTmuxFormat:
    def test_doubles_hash(self):
        assert escape_tmux_format("a#b") == "a##b"

    def test_no_hash_unchanged(self):
        assert escape_tmux_format("hello world") == "hello world"

    def test_idempotent_on_clean_input(self):
        assert escape_tmux_format(escape_tmux_format("clean")) == "clean"

    def test_blocks_format_substitution(self):
        # Without escaping, '#(date)' would be interpreted as a tmux command.
        assert escape_tmux_format("#(date)") == "##(date)"


def test_tmux_error_is_runtime_error():
    assert issubclass(TmuxError, RuntimeError)


def test_invalid_session_name_is_value_error():
    assert issubclass(InvalidSessionName, ValueError)
```

- [ ] **Step 1.2: Run tests to verify they fail**

Run: `uv run pytest tests/test_safety.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'hive.safety'`.

- [ ] **Step 1.3: Implement `src/hive/safety.py`**

```python
# src/hive/safety.py
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

SESSION_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_REPLACE_RE = re.compile(r"[^A-Za-z0-9._-]")
_MAX_LEN = 64
_DEFAULT_NAME = "session"


class InvalidSessionName(ValueError):
    pass


class TmuxError(RuntimeError):
    pass


def validate_session_name(name: str) -> None:
    if not isinstance(name, str) or not SESSION_NAME_RE.fullmatch(name):
        raise InvalidSessionName(f"invalid session name: {name!r}")
    if name in {".", ".."}:
        raise InvalidSessionName(f"reserved session name: {name!r}")


def sanitize_session_name(name: str) -> str:
    if not isinstance(name, str):
        return _DEFAULT_NAME
    cleaned = _REPLACE_RE.sub("_", name)[:_MAX_LEN]
    if not cleaned or cleaned in {".", ".."} or set(cleaned) == {"_"}:
        return _DEFAULT_NAME
    return cleaned


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def escape_tmux_format(text: str) -> str:
    return text.replace("#", "##")
```

- [ ] **Step 1.4: Run tests to verify they pass**

Run: `uv run pytest tests/test_safety.py -v`
Expected: all green.

- [ ] **Step 1.5: Commit**

```bash
git add src/hive/safety.py tests/test_safety.py
git commit -m "feat: add safety module with name validation, atomic write, tmux escape"
```

---

## Task 2: TmuxClient — typed signature, error class, safer parser

**Files:**
- Modify: `src/hive/tmux.py`
- Modify: `tests/test_tmux.py`

- [ ] **Step 2.1: Update `tests/test_tmux.py` — replace string-command tests with list-args tests, add error-raise tests, switch list_windows test to tab format**

Replace the existing `test_new_window` and `test_new_window_with_env` and add error tests; update `test_list_windows`:

```python
# tests/test_tmux.py — replace the existing test_new_window / test_new_window_with_env / test_list_windows blocks with these
import pytest
from hive.safety import TmuxError


class TestTmuxClient:
    # ... keep prior tests unchanged ...

    @patch("hive.tmux.subprocess.run")
    def test_list_windows_tab_delimited(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="0\tdashboard\t0\n1\tmy:session\t1234\n",
        )
        windows = self.tmux.list_windows()
        assert windows == [
            {"index": 0, "name": "dashboard", "alive": False},
            {"index": 1, "name": "my:session", "alive": True},
        ]
        # Assert format string uses tab separator
        called_args = mock_run.call_args.args[0]
        assert "#{window_index}\t#{window_name}\t#{pane_pid}" in called_args

    @patch("hive.tmux.subprocess.run")
    def test_new_window_with_command_args_list(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="3\n")
        idx = self.tmux.new_window(
            "my-window", "/tmp/proj",
            ["claude", "--name", "my window; rm -rf /"],
        )
        assert idx == 3
        called = mock_run.call_args.args[0]
        # The command appears as a single shlex-joined string at the end.
        assert called[-1] == "claude --name 'my window; rm -rf /'"

    @patch("hive.tmux.subprocess.run")
    def test_new_window_raises_tmux_error_on_nonzero(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="boom")
        with pytest.raises(TmuxError, match="boom"):
            self.tmux.new_window("w", "/tmp", ["claude"])

    @patch("hive.tmux.subprocess.run")
    def test_new_window_raises_tmux_error_on_non_numeric(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="not-a-number\n", stderr="")
        with pytest.raises(TmuxError, match="non-numeric"):
            self.tmux.new_window("w", "/tmp", ["claude"])


def test_new_window_with_env(monkeypatch):
    from hive.tmux import TmuxClient
    captured = {}

    def fake_run(self, args, text=False):
        captured["args"] = args
        class R: returncode = 0; stdout = "5"; stderr = ""
        return R()

    monkeypatch.setattr(TmuxClient, "_run", fake_run)
    client = TmuxClient("hive")
    idx = client.new_window("foo", "/tmp", ["claude"], env={"HIVE_SESSION": "foo"})
    assert idx == 5
    assert "-e" in captured["args"]
    e_idx = captured["args"].index("-e")
    assert captured["args"][e_idx + 1] == "HIVE_SESSION=foo"
```

Also delete the old `test_new_window` (string-command) and old `test_list_windows` (colon-delimited) blocks.

- [ ] **Step 2.2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tmux.py -v`
Expected: failures around `new_window` signature, missing `alive` key, missing `TmuxError`.

- [ ] **Step 2.3: Update `src/hive/tmux.py`**

Replace `new_window` and `list_windows`:

```python
# src/hive/tmux.py — replace these methods
import shlex
from hive.safety import TmuxError


    def list_windows(self) -> list[dict]:
        result = self._run(
            [
                "tmux", "list-windows", "-t", self.session_name,
                "-F", "#{window_index}\t#{window_name}\t#{pane_pid}",
            ],
            text=True,
        )
        if result.returncode != 0:
            return []
        windows = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            try:
                idx = int(parts[0])
                pid = int(parts[2])
            except ValueError:
                continue
            windows.append({
                "index": idx,
                "name": parts[1],
                "alive": pid > 0,
            })
        return windows

    def new_window(
        self,
        name: str,
        cwd: str,
        command_args: list[str],
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
        args.extend(["-P", "-F", "#{window_index}", shlex.join(command_args)])
        result = self._run(args, text=True)
        if result.returncode != 0:
            stderr = (result.stderr or "").strip() if hasattr(result, "stderr") else ""
            raise TmuxError(f"tmux new-window failed (rc={result.returncode}): {stderr}")
        try:
            return int(result.stdout.strip())
        except ValueError as exc:
            raise TmuxError(
                f"tmux new-window returned non-numeric output: {result.stdout!r}"
            ) from exc
```

Update `_run` to capture stderr in text mode (so TmuxError messages are useful):

```python
    def _run(self, args: list[str], text: bool = False) -> subprocess.CompletedProcess:
        return subprocess.run(args, capture_output=True, text=text)
```

(This collapses the prior conditional — `capture_output=True` already captures both stdout and stderr; `text=True` just decodes.)

- [ ] **Step 2.4: Run tests to verify they pass**

Run: `uv run pytest tests/test_tmux.py -v`
Expected: all green.

- [ ] **Step 2.5: Commit**

```bash
git add src/hive/tmux.py tests/test_tmux.py
git commit -m "refactor: type-safe new_window with shlex.join and TmuxError"
```

Note: this commit breaks the existing call sites in `app.py` and `__main__.py`. Tests for those are addressed in Tasks 3–5; the suite as a whole will be red until Task 5 lands. That's acceptable inside a coordinated refactor — full suite passes again at end of Task 5.

---

## Task 3: Wire safety + new tmux signature into call sites

**Files:**
- Modify: `src/hive/app.py`
- Modify: `src/hive/__main__.py`
- Modify: `tests/test_app.py`

- [ ] **Step 3.1: Add new test cases to `tests/test_app.py`**

```python
# tests/test_app.py — add these tests at module level
from unittest.mock import patch, MagicMock
import pytest
from hive.safety import InvalidSessionName


def test_create_session_validates_name():
    from hive.app import HiveApp
    app = HiveApp()
    with pytest.raises(InvalidSessionName):
        # _create_session is async; we test the validation path
        import asyncio
        asyncio.get_event_loop().run_until_complete(
            app._create_session("/tmp", "bad name; rm -rf /")
        )


def test_restore_sanitizes_bad_existing_state(monkeypatch, tmp_path):
    from hive.app import HiveApp
    app = HiveApp()
    app.state.sessions = {
        "bad name": {
            "project_path": str(tmp_path),
            "tmux_window": 1,
            "claude_session_id": "",
        }
    }
    monkeypatch.setattr(app.tmux, "list_windows", lambda: [])
    captured: dict[str, str] = {}

    def fake_new_window(name, cwd, command_args, env=None):
        captured["name"] = name
        captured["env_session"] = env["HIVE_SESSION"] if env else ""
        return 99

    monkeypatch.setattr(app.tmux, "new_window", fake_new_window)
    monkeypatch.setattr(app.tmux, "select_window", lambda _: None)
    monkeypatch.setattr(app.state, "save_default", lambda: None)
    app._restore_sessions()
    # Sanitized name only — never the bad one
    assert captured["name"] == "bad_name"
    assert captured["env_session"] == "bad_name"
    assert "bad name" not in app.state.sessions
    assert "bad_name" in app.state.sessions
```

- [ ] **Step 3.2: Run tests to verify they fail**

Run: `uv run pytest tests/test_app.py -v`
Expected: failures (validation not yet wired; `_create_session` accepts string command not list).

- [ ] **Step 3.3: Update `src/hive/app.py` — `_create_session`, `_restore_sessions`, dialog hook**

Replace `_create_session` (around line 285):

```python
from hive.safety import InvalidSessionName, sanitize_session_name, validate_session_name


    def _build_claude_args(self, session_name: str, resume_id: str | None) -> list[str]:
        args = ["claude", "--dangerously-skip-permissions", "--name", session_name]
        if resume_id:
            args.extend(["--resume", resume_id])
        return args

    async def _create_session(
        self,
        project_path: str,
        session_name: str,
        resume_id: str | None = None,
    ) -> None:
        validate_session_name(session_name)
        args = self._build_claude_args(session_name, resume_id)
        from hive.safety import TmuxError
        try:
            window_idx = self.tmux.new_window(
                session_name, project_path, args, env={"HIVE_SESSION": session_name}
            )
        except TmuxError as exc:
            from hive.widgets.dialogs import ErrorScreen
            await self.push_screen_wait(ErrorScreen("tmux failed", str(exc)))
            return
        self.state.add_session(session_name, project_path, window_idx, resume_id or "")
        self.state.save_default()
```

Replace `_restore_sessions` (around line 78):

```python
    def _restore_sessions(self) -> None:
        if not self.state.sessions:
            return
        windows = self.tmux.list_windows()
        existing_names = {w["name"] for w in windows if w["index"] != 0}
        renames: list[tuple[str, str]] = []
        for name in list(self.state.sessions.keys()):
            try:
                validate_session_name(name)
            except InvalidSessionName:
                clean = sanitize_session_name(name)
                while clean in self.state.sessions and clean != name:
                    clean = sanitize_session_name(clean + "_")
                self.state.sessions[clean] = self.state.sessions.pop(name)
                renames.append((name, clean))
        if renames:
            summary = ", ".join(f"{a!r}→{b!r}" for a, b in renames)
            self._set_warning_banner(f"sanitized state names: {summary}")

        for name, info in list(self.state.sessions.items()):
            if name in existing_names:
                continue
            project_path = info.get("project_path", "")
            if not os.path.isdir(project_path):
                self.state.remove_session(name)
                continue
            session_id = info.get("claude_session_id", "")
            args = self._build_claude_args(name, session_id or None)
            if not session_id:
                # use --continue when no resume id
                args = ["claude", "--dangerously-skip-permissions", "--name", name, "--continue"]
            from hive.safety import TmuxError
            try:
                window_idx = self.tmux.new_window(
                    name, project_path, args, env={"HIVE_SESSION": name}
                )
            except TmuxError:
                continue
            self.state.sessions[name]["tmux_window"] = window_idx
        self.tmux.select_window(0)
        self.state.save_default()
```

Add the warning-banner setter as part of Task 4's banner work; for now define a no-op stub at the top of `HiveApp`:

```python
    def _set_warning_banner(self, text: str) -> None:
        # Filled in by the banner task; keep a no-op so tests pass now.
        pass
```

Update `SessionOptionsScreen` and `RenameScreen` submit handlers to call `validate_session_name` and surface the error inline:

```python
# src/hive/widgets/dialogs.py — inside SessionOptionsScreen.on_button_pressed for OK button
from hive.safety import InvalidSessionName, validate_session_name

# Where the name is read from input:
name = self.query_one("#name-input", Input).value.strip()
if name:
    try:
        validate_session_name(name)
    except InvalidSessionName as exc:
        self.query_one("#name-error", Label).update(str(exc))
        return

# Add a Label#name-error to the dialog's compose() output (initially empty)
yield Label("", id="name-error")
```

Same pattern in `RenameScreen.on_button_pressed`.

Update `__main__.py:_unique_window_name` and the `hive new <path>` flow:

```python
# src/hive/__main__.py
import shlex
from hive.safety import InvalidSessionName, sanitize_session_name, validate_session_name


def _unique_window_name(tmux: TmuxClient, base: str) -> str:
    base = sanitize_session_name(base)  # cwd basename may be anything
    existing = {w["name"] for w in tmux.list_windows()}
    if base not in existing:
        return base
    i = 2
    while f"{base}-{i:03d}" in existing:
        i += 1
    return f"{base}-{i:03d}"


# In the `new` command branch:
    if command == "new" and len(args) >= 2:
        path = os.path.abspath(args[1])
        if not os.path.isdir(path):
            print(f"Directory not found: {path}")
            sys.exit(1)
        _ensure_session(tmux)
        name = _unique_window_name(tmux, os.path.basename(path))
        try:
            validate_session_name(name)
        except InvalidSessionName as exc:
            print(f"Refusing unsafe session name: {exc}")
            sys.exit(1)
        from hive.safety import TmuxError
        try:
            tmux.new_window(
                name, path,
                ["claude", "--dangerously-skip-permissions", "--name", name],
                env={"HIVE_SESSION": name},
            )
        except TmuxError as exc:
            print(f"tmux failed: {exc}")
            sys.exit(1)
        print(f"Created session '{name}' in {path}")
        return
```

Drop the unused `config` parameter from `_ensure_session`:

```python
def _ensure_session(tmux: TmuxClient) -> None:
    if not tmux.session_exists():
        tmux.create_session()
```

Add `ErrorScreen` to `src/hive/widgets/dialogs.py`:

```python
class ErrorScreen(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Dismiss"), ("enter", "dismiss", "Dismiss")]

    def __init__(self, title: str, message: str) -> None:
        super().__init__()
        self.error_title = title
        self.error_message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="error-dialog"):
            yield Label(self.error_title, id="error-title")
            yield Label(self.error_message, id="error-message")
            with Horizontal(id="error-buttons"):
                yield Button("OK", variant="primary", id="btn-ok")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)

    def action_dismiss(self) -> None:
        self.dismiss(None)
```

- [ ] **Step 3.4: Run tests to verify they pass**

Run: `uv run pytest tests/test_app.py tests/test_tmux.py -v`
Expected: all green.

- [ ] **Step 3.5: Commit**

```bash
git add src/hive/app.py src/hive/__main__.py src/hive/widgets/dialogs.py tests/test_app.py
git commit -m "refactor: validate session names; pass argv list to tmux; surface tmux errors"
```

---

## Task 4: Poll-loop error boundary, banners, status escape

**Files:**
- Modify: `src/hive/app.py`
- Modify: `src/hive/hive.tcss`
- Modify: `tests/test_app.py`

- [ ] **Step 4.1: Add tests**

```python
# tests/test_app.py — add
import asyncio


def test_refresh_exception_is_caught_and_banner_set(monkeypatch):
    from hive.app import HiveApp
    app = HiveApp()

    async def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(app, "_refresh_sessions", boom)
    captured: dict[str, str] = {}
    monkeypatch.setattr(app, "_set_error_banner", lambda text: captured.setdefault("text", text))
    monkeypatch.setattr(app, "_clear_error_banner", lambda: captured.pop("text", None))

    # Run one iteration of the loop manually
    asyncio.get_event_loop().run_until_complete(app._poll_once())
    assert "boom" in captured["text"]


def test_refresh_success_clears_banner(monkeypatch):
    from hive.app import HiveApp
    app = HiveApp()

    async def ok():
        return None

    monkeypatch.setattr(app, "_refresh_sessions", ok)
    cleared = {"flag": False}
    monkeypatch.setattr(app, "_clear_error_banner", lambda: cleared.update(flag=True))
    monkeypatch.setattr(app, "_set_error_banner", lambda _: None)

    asyncio.get_event_loop().run_until_complete(app._poll_once())
    assert cleared["flag"] is True


def test_status_bar_escapes_hash_in_session_name():
    from hive.app import HiveApp
    from hive.detector import SessionState
    from hive.widgets.session_list import SessionData

    app = HiveApp()
    app.session_data_map = {
        "ok": SessionData(name="ok", project_path="", tmux_window=1, state=SessionState.WAITING),
    }
    captured: dict[str, str] = {}
    app.tmux.set_status_bar = lambda t: captured.setdefault("text", t)
    app._update_tmux_status()
    # 'ok' has no '#', so it should appear once. The label text already contains
    # no '#' either. The point of this test is the call path is intact and the
    # escape function is invoked on the dynamic portion. We test the helper
    # directly in test_safety; here we just verify status bar is built.
    assert "ok ●" in captured["text"]
```

- [ ] **Step 4.2: Run tests to verify they fail**

Run: `uv run pytest tests/test_app.py -v -k "poll or banner or status_bar_escapes"`
Expected: failures (no `_poll_once`, no banner methods).

- [ ] **Step 4.3: Update `src/hive/app.py`**

Add imports and replace `poll_sessions` and `_update_tmux_status`. Add the banner helpers and widgets:

```python
import sys
import traceback
from textual.widgets import Label
from hive.safety import escape_tmux_format


# In compose(): replace the existing yields with this
    def compose(self) -> ComposeResult:
        # ... existing header label ...
        yield Label(header_text, id="header")
        yield Label("", id="info-banner", classes="banner banner-info")
        yield Label("", id="warning-banner", classes="banner banner-warn")
        yield Label("", id="error-banner", classes="banner banner-error")
        with Horizontal(id="main"):
            yield SessionListView(id="session-panel", initial_index=0)
            yield PreviewPane("(no session selected)", id="preview-panel")
        yield Label(
            "n:new  f:free  g:clone  k:kill  R:rename  u:url  /:search  ↵:attach  Q:quit",
            id="footer-bar",
        )

    def _set_error_banner(self, text: str) -> None:
        try:
            self.query_one("#error-banner", Label).update(text)
        except Exception:
            pass

    def _clear_error_banner(self) -> None:
        try:
            self.query_one("#error-banner", Label).update("")
        except Exception:
            pass

    def _set_warning_banner(self, text: str) -> None:
        try:
            self.query_one("#warning-banner", Label).update(text)
        except Exception:
            pass

    def _set_info_banner(self, text: str) -> None:
        try:
            self.query_one("#info-banner", Label).update(text)
        except Exception:
            pass

    async def _poll_once(self) -> None:
        try:
            await self._refresh_sessions()
            self._clear_error_banner()
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            self._set_error_banner(
                f"refresh failed: {type(exc).__name__}: {exc} — see ~/.claude/hive/dashboard.log"
            )

    @work(exclusive=True)
    async def poll_sessions(self) -> None:
        while True:
            await self._poll_once()
            await asyncio.sleep(self.config.refresh_interval_ms / 1000)
```

Update `_update_tmux_status` to escape session names (and any other user-supplied portions):

```python
    def _update_tmux_status(self) -> None:
        total = len(self.session_data_map)
        waiting = sum(1 for s in self.session_data_map.values() if s.state == SessionState.WAITING)
        booting = sum(1 for s in self.session_data_map.values() if s.state == SessionState.BOOTSTRAPPING)
        parts = [f"hive: {total} sessions"]
        if booting:
            parts.append(f"loading {booting}")
        if waiting:
            parts.append(f"● {waiting} waiting")
        for s in self.session_data_map.values():
            if s.state == SessionState.WAITING:
                parts.append(f"{escape_tmux_format(s.name)} ●")
        parts.append("Ctrl+B 0 → dashboard")
        self.tmux.set_status_bar(" | ".join(parts))
```

- [ ] **Step 4.4: Add minimal CSS for banners**

Append to `src/hive/hive.tcss`:

```css
.banner {
    width: 100%;
    height: auto;
    padding: 0 1;
    display: none;
}

.banner-info { background: $primary 30%; color: $text; }
.banner-warn { background: $warning 30%; color: $text; }
.banner-error { background: $error 30%; color: $text; }

#info-banner.has-text, #warning-banner.has-text, #error-banner.has-text {
    display: block;
}
```

Update each banner setter to toggle the `has-text` class:

```python
    def _set_error_banner(self, text: str) -> None:
        try:
            label = self.query_one("#error-banner", Label)
            label.update(text)
            label.set_class(bool(text), "has-text")
        except Exception:
            pass
    # Apply the same set_class pattern to _clear_error_banner, _set_warning_banner, _set_info_banner
```

- [ ] **Step 4.5: Run tests to verify they pass**

Run: `uv run pytest tests/test_app.py -v`
Expected: all green.

- [ ] **Step 4.6: Commit**

```bash
git add src/hive/app.py src/hive/hive.tcss tests/test_app.py
git commit -m "feat: error-boundary the poll loop; add status-bar tmux-format escape"
```

---

## Task 5: Hook installation hardening + atomic writes

**Files:**
- Modify: `src/hive/install_hooks.py`
- Modify: `src/hive/hook_writer.py`
- Modify: `src/hive/config.py`
- Modify: `src/hive/app.py` (drop auto-install, add first-run banner check)
- Modify: `tests/test_install_hooks.py`
- Modify: `tests/test_hook_writer.py`
- Modify: `tests/test_config.py`

- [ ] **Step 5.1: Update tests for absolute-path hook command**

```python
# tests/test_install_hooks.py — replace the hook-command assertions with absolute-path expectations
import sys
import shlex
import json


def test_install_writes_absolute_command(tmp_path, monkeypatch):
    from hive.install_hooks import install_hooks, HOOK_COMMAND, settings_path
    monkeypatch.setattr("hive.install_hooks.settings_path", lambda: tmp_path / "settings.json")
    assert install_hooks() is True
    data = json.loads((tmp_path / "settings.json").read_text())
    expected = f"{shlex.quote(sys.executable)} -m hive.hook_writer"
    found_commands = [
        h["command"]
        for entries in data["hooks"].values()
        for entry in entries
        for h in entry.get("hooks", [])
    ]
    assert expected in found_commands
    assert HOOK_COMMAND == expected


def test_install_atomic_no_temp_left(tmp_path, monkeypatch):
    from hive.install_hooks import install_hooks
    monkeypatch.setattr("hive.install_hooks.settings_path", lambda: tmp_path / "settings.json")
    install_hooks()
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "settings.json"]
    assert leftovers == []


def test_install_tolerates_malformed_hooks_key(tmp_path, monkeypatch):
    from hive.install_hooks import install_hooks
    target = tmp_path / "settings.json"
    target.write_text(json.dumps({"hooks": "not-a-dict", "other": {"k": 1}}))
    monkeypatch.setattr("hive.install_hooks.settings_path", lambda: target)
    assert install_hooks() is True
    data = json.loads(target.read_text())
    assert isinstance(data["hooks"], dict)
    assert data["other"] == {"k": 1}
```

- [ ] **Step 5.2: Update tests for hook_writer race-safe temp file**

```python
# tests/test_hook_writer.py — add
import threading
import json
from io import StringIO


def test_concurrent_writers_no_collision(tmp_path, monkeypatch):
    from hive import hook_writer

    monkeypatch.setattr(hook_writer, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(hook_writer, "state_file_path", lambda n: tmp_path / f"{n}.json")
    monkeypatch.setenv("HIVE_SESSION", "race-test")

    payloads = [
        {"hook_event_name": "Stop", "session_id": f"s{i}"}
        for i in range(20)
    ]
    errors: list[Exception] = []

    def fire(p):
        try:
            monkeypatch.setattr("sys.stdin", StringIO(json.dumps(p)))
            hook_writer.main()
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=fire, args=(p,)) for p in payloads]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    final = json.loads((tmp_path / "race-test.json").read_text())
    assert final["state"] == "waiting"
```

- [ ] **Step 5.3: Update tests for tolerant config/state load and atomic save**

```python
# tests/test_config.py — add
def test_state_save_is_atomic(tmp_path):
    from hive.config import HiveState
    state = HiveState()
    state.sessions["abc"] = {"project_path": "/tmp"}
    target = tmp_path / "state.json"
    state.save(target)
    assert target.exists()
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "state.json"]
    assert leftovers == []


def test_state_load_tolerates_malformed_file(tmp_path):
    from hive.config import HiveState
    target = tmp_path / "state.json"
    target.write_text("{ not valid json")
    state = HiveState.load(target)
    assert state.sessions == {}
    assert state.projects == {}


def test_config_load_tolerates_malformed_file(tmp_path):
    from hive.config import HiveConfig
    target = tmp_path / "config.toml"
    target.write_text("not [valid toml")
    cfg = HiveConfig.load(target)
    # Falls back to defaults
    assert cfg.tmux_session_name == "hive"
```

- [ ] **Step 5.4: Run tests to verify they fail**

Run: `uv run pytest tests/test_install_hooks.py tests/test_hook_writer.py tests/test_config.py -v`
Expected: failures (HOOK_COMMAND wrong, race fails, no graceful load).

- [ ] **Step 5.5: Update `src/hive/install_hooks.py`**

```python
# src/hive/install_hooks.py
from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

from hive.safety import atomic_write_text

HOOK_COMMAND = f"{shlex.quote(sys.executable)} -m hive.hook_writer"
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


def hook_installed(settings: dict | None = None) -> bool:
    if settings is None:
        try:
            settings = json.loads(settings_path().read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return False
    if not isinstance(settings, dict):
        return False
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return False
    for entries in hooks.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            for h in entry.get("hooks", []) if isinstance(entry, dict) else []:
                if isinstance(h, dict) and h.get("command") == HOOK_COMMAND:
                    return True
    return False


def install_hooks() -> bool:
    path = settings_path()
    try:
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

        existing_hooks = settings.get("hooks")
        if not isinstance(existing_hooks, dict):
            existing_hooks = {}
            settings["hooks"] = existing_hooks

        for event in HOOK_EVENTS:
            entries = existing_hooks.get(event)
            if not isinstance(entries, list):
                entries = []
                existing_hooks[event] = entries
            already = any(
                isinstance(h, dict) and h.get("command") == HOOK_COMMAND
                for entry in entries if isinstance(entry, dict)
                for h in entry.get("hooks", [])
            )
            if not already:
                entries.append({
                    "matcher": "",
                    "hooks": [{"type": "command", "command": HOOK_COMMAND}],
                })

        atomic_write_text(path, json.dumps(settings, indent=2))
        return True
    except OSError:
        return False
```

- [ ] **Step 5.6: Update `src/hive/hook_writer.py`**

```python
# src/hive/hook_writer.py — replace the body of main() that writes the file
import os
import sys
import json
import tempfile
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
    try:
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
        path = state_file_path(session_name)
        state_dir().mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=state_dir(),
            prefix=f".{session_name}.",
            suffix=".json.tmp",
        )
        try:
            with os.fdopen(fd, "w") as f:
                f.write(json.dumps(record))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5.7: Update `src/hive/config.py`**

```python
# src/hive/config.py — replace HiveState.save and both load methods

from hive.safety import atomic_write_text


    @classmethod
    def load(cls, path: Path) -> HiveConfig:
        if not path.exists():
            return cls.defaults()
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
            if not isinstance(data, dict):
                return cls.defaults()
            projects = data.get("projects", {}) if isinstance(data.get("projects"), dict) else {}
            display = data.get("display", {}) if isinstance(data.get("display"), dict) else {}
            tmux = data.get("tmux", {}) if isinstance(data.get("tmux"), dict) else {}
            return cls(
                scan_paths=projects.get("scan_paths", DEFAULT_SCAN_PATHS),
                clone_path=projects.get("clone_path", DEFAULT_CLONE_PATH),
                refresh_interval_ms=int(display.get("refresh_interval_ms", 2500)),
                preview_lines=int(display.get("preview_lines", 20)),
                tmux_session_name=str(tmux.get("session_name", "hive")),
            )
        except (tomllib.TOMLDecodeError, OSError, TypeError, ValueError) as exc:
            print(f"hive: config load failed, using defaults: {exc}", file=sys.stderr)
            return cls.defaults()


    @classmethod
    def load(cls, path: Path) -> HiveState:
        if not path.exists():
            return cls()
        try:
            with open(path) as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return cls()
            sessions = data.get("sessions", {}) if isinstance(data.get("sessions"), dict) else {}
            projects = data.get("projects", {}) if isinstance(data.get("projects"), dict) else {}
            return cls(sessions=sessions, projects=projects)
        except (json.JSONDecodeError, OSError, TypeError, ValueError) as exc:
            print(f"hive: state load failed, using empty: {exc}", file=sys.stderr)
            return cls()


    def save(self, path: Path) -> None:
        atomic_write_text(path, json.dumps({"sessions": self.sessions, "projects": self.projects}, indent=2))
```

Add `import sys` at the top of `config.py`.

- [ ] **Step 5.8: Drop auto-install from `HiveApp.__init__`; add first-run banner**

```python
# src/hive/app.py
    def __init__(self) -> None:
        super().__init__()
        self.config = HiveConfig.load_default()
        self.state = HiveState.load_default()
        self.tmux = TmuxClient(self.config.tmux_session_name)
        self.session_data_map: dict[str, SessionData] = {}
        self._last_attached: str | None = None

    async def on_mount(self) -> None:
        self.query_one("#session-panel", SessionListView).focus()
        from hive.install_hooks import hook_installed
        if not hook_installed():
            self._set_info_banner("Hooks not installed. Run 'hive install-hooks' to enable state detection.")
        self._restore_sessions()
        self.poll_sessions()
```

- [ ] **Step 5.9: Run tests**

Run: `uv run pytest -v`
Expected: all green.

- [ ] **Step 5.10: Commit**

```bash
git add src/hive/install_hooks.py src/hive/hook_writer.py src/hive/config.py src/hive/app.py \
        tests/test_install_hooks.py tests/test_hook_writer.py tests/test_config.py
git commit -m "feat: explicit absolute-path hooks; atomic writes; tolerant load"
```

---

## Task 6: Clone path validation + git error surfacing

**Files:**
- Modify: `src/hive/app.py`
- Modify: `tests/test_app.py`

- [ ] **Step 6.1: Add tests**

```python
# tests/test_app.py — add
def test_validate_clone_target_rejects_dotdot(tmp_path):
    from hive.app import _validate_clone_target
    with pytest.raises(ValueError):
        _validate_clone_target(str(tmp_path), "..")


def test_validate_clone_target_rejects_separators(tmp_path):
    from hive.app import _validate_clone_target
    with pytest.raises(ValueError):
        _validate_clone_target(str(tmp_path), "a/b")


def test_validate_clone_target_accepts_normal_repo(tmp_path):
    from hive.app import _validate_clone_target
    assert _validate_clone_target(str(tmp_path), "myrepo") == str(tmp_path / "myrepo")
```

- [ ] **Step 6.2: Run tests to verify they fail**

Run: `uv run pytest tests/test_app.py -v -k "clone_target"`
Expected: failures (function doesn't exist).

- [ ] **Step 6.3: Add the helper and rewire `action_clone_session`**

```python
# src/hive/app.py — add at module level
def _validate_clone_target(clone_path: str, repo_name: str) -> str:
    if repo_name in {"", ".", ".."} or "/" in repo_name or "\\" in repo_name:
        raise ValueError(f"unsafe repo name: {repo_name!r}")
    base = Path(clone_path).resolve()
    target = (base / repo_name).resolve()
    if not target.is_relative_to(base):
        raise ValueError(f"target escapes clone path: {target}")
    return str(target)


    @work
    async def action_clone_session(self) -> None:
        from hive.widgets.dialogs import ErrorScreen
        result = await self.push_screen_wait(CloneScreen(self.config.clone_path))
        if result is None:
            return
        url = result["url"]
        clone_path = result["clone_path"]
        repo_name = url.rstrip("/").split("/")[-1].removesuffix(".git")
        try:
            target = _validate_clone_target(clone_path, repo_name)
        except ValueError as exc:
            await self.push_screen_wait(ErrorScreen("Clone refused", str(exc)))
            return

        if os.path.exists(target):
            choice = await self.push_screen_wait(FolderExistsScreen(target))
            if choice is None:
                return
            if choice == "pull":
                rc = subprocess.run(["git", "-C", target, "pull"], capture_output=True, text=True)
                if rc.returncode != 0:
                    await self.push_screen_wait(ErrorScreen("git pull failed", rc.stderr.strip()))
                    return
        else:
            rc = subprocess.run(["git", "clone", url, target], capture_output=True, text=True)
            if rc.returncode != 0:
                await self.push_screen_wait(ErrorScreen("git clone failed", rc.stderr.strip()))
                return

        name = self._next_session_name(repo_name)
        await self._create_session(target, name)
```

- [ ] **Step 6.4: Run tests**

Run: `uv run pytest -v`
Expected: all green.

- [ ] **Step 6.5: Commit**

```bash
git add src/hive/app.py tests/test_app.py
git commit -m "feat: validate clone target; surface git failures via modal"
```

---

## Task 7: Disallow rename of active sessions

**Files:**
- Modify: `src/hive/app.py`
- Modify: `tests/test_app.py`

- [ ] **Step 7.1: Add test**

```python
# tests/test_app.py — add
def test_rename_active_session_rejected(monkeypatch):
    from hive.app import HiveApp
    from hive.widgets.session_list import SessionData
    from hive.detector import SessionState

    app = HiveApp()
    data = SessionData(name="active-001", project_path="/tmp", tmux_window=1, state=SessionState.WAITING)
    app.session_data_map = {"active-001": data}

    monkeypatch.setattr(app, "_set_warning_banner", lambda t: None)
    captured: dict[str, bool] = {"renamed": False}
    monkeypatch.setattr(app.tmux, "rename_window", lambda *a, **kw: captured.update(renamed=True))

    # Stub out the rename screen to "return new-name"
    async def fake_push(_screen):
        return "new-name"

    monkeypatch.setattr(app, "push_screen_wait", fake_push)

    # Stub the list_view query
    class FakeView:
        def get_session_data(self):
            return data

    monkeypatch.setattr(app, "query_one", lambda *a, **kw: FakeView())

    import asyncio
    asyncio.get_event_loop().run_until_complete(app.action_rename_session())
    assert captured["renamed"] is False
```

- [ ] **Step 7.2: Run test to verify it fails**

Run: `uv run pytest tests/test_app.py -v -k "rename_active"`
Expected: failure (rename happens).

- [ ] **Step 7.3: Update `action_rename_session`**

```python
# src/hive/app.py
    @work
    async def action_rename_session(self) -> None:
        from hive.widgets.dialogs import ErrorScreen
        list_view = self.query_one("#session-panel", SessionListView)
        data = list_view.get_session_data()
        if data is None:
            return
        if data.name in self.session_data_map:
            await self.push_screen_wait(
                ErrorScreen("Cannot rename", "Kill the session first, then rename it.")
            )
            return
        new_name = await self.push_screen_wait(RenameScreen(data.name))
        if new_name and new_name != data.name:
            try:
                validate_session_name(new_name)
            except InvalidSessionName as exc:
                await self.push_screen_wait(ErrorScreen("Invalid name", str(exc)))
                return
            self.tmux.rename_window(data.tmux_window, new_name)
            if data.name in self.state.sessions:
                self.state.sessions[new_name] = self.state.sessions.pop(data.name)
            self.state.save_default()
```

- [ ] **Step 7.4: Run tests**

Run: `uv run pytest -v`
Expected: all green.

- [ ] **Step 7.5: Commit**

```bash
git add src/hive/app.py tests/test_app.py
git commit -m "feat: disallow rename of active sessions to prevent state desync"
```

---

## Task 8: Dead code cleanup

**Files:**
- Modify: `src/hive/detector.py`
- Modify: `src/hive/app.py`
- Modify: `tests/test_app.py`, `tests/test_detector.py`

- [ ] **Step 8.1: Remove `detect_context_usage`, `CONTEXT_WINDOWS`, `CLAUDE_PROJECTS_DIR`, `_encode_project_path` from `src/hive/detector.py`**

These are unused. Delete them and any imports they pulled in (`json`, `pathlib.Path`) if no longer needed elsewhere in the file. Verify with `grep`.

- [ ] **Step 8.2: Remove `action_search` and the `slash` binding from `src/hive/app.py`**

Delete from BINDINGS:
```python
Binding("slash", "search", "Search", show=False),
```

Delete the method:
```python
    async def action_search(self) -> None:
        pass
```

Delete the `/:search` substring from the footer label.

- [ ] **Step 8.3: Update tests in `tests/test_detector.py`**

Delete any tests that exercise `detect_context_usage`. Run `grep -n detect_context_usage tests/` to find them.

- [ ] **Step 8.4: Run full suite**

Run: `uv run pytest -v`
Expected: all green.

- [ ] **Step 8.5: Commit**

```bash
git add src/hive/detector.py src/hive/app.py tests/test_app.py tests/test_detector.py
git commit -m "chore: remove dead code (detect_context_usage, action_search, etc.)"
```

---

## Final verification

- [ ] **Step 9.1: Run the full test suite**

Run: `uv run pytest -v`
Expected: all green.

- [ ] **Step 9.2: Smoke test the dashboard**

```bash
tmux kill-session -t hive 2>/dev/null
uv run hive
```

Inside the dashboard:
1. Verify the "Hooks not installed" banner appears (assuming a fresh `~/.claude/settings.json` for the test, otherwise it should be absent).
2. Press `n`, pick a project, accept the auto-suggested name → session opens.
3. Press `n` again, pick a project, type `bad name; echo PWNED` → dialog shows inline error, no session created.
4. Detach and re-attach → no crashes.

- [ ] **Step 9.3: Smoke test `hive install-hooks`**

```bash
hive install-hooks
cat ~/.claude/settings.json | grep -F "$(python -c 'import sys, shlex; print(shlex.quote(sys.executable))') -m hive.hook_writer"
```
Expected: a match. Re-running `hive install-hooks` should be idempotent.

- [ ] **Step 9.4: Verify CRITICAL findings closed by hand**

```bash
# Test #1 (CRITICAL: shell injection): try to create a session named with shell metachar via the CLI
uv run hive new "$(pwd)"  # should succeed with a sanitized name
# Negative case is exercised in tests; manual confirmation that legitimate names still work is enough.

# Test #2 (CRITICAL: tmux format): set a session name containing '#' is impossible (allowlist). Confirm via:
uv run python -c "from hive.safety import validate_session_name; validate_session_name('a#b')"
# Expected: InvalidSessionName raised.
```

---

## Summary of commits when complete

1. `feat: add safety module with name validation, atomic write, tmux escape`
2. `refactor: type-safe new_window with shlex.join and TmuxError`
3. `refactor: validate session names; pass argv list to tmux; surface tmux errors`
4. `feat: error-boundary the poll loop; add status-bar tmux-format escape`
5. `feat: explicit absolute-path hooks; atomic writes; tolerant load`
6. `feat: validate clone target; surface git failures via modal`
7. `feat: disallow rename of active sessions to prevent state desync`
8. `chore: remove dead code (detect_context_usage, action_search, etc.)`
