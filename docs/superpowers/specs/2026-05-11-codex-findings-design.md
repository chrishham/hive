# Codex Findings Cleanup — Design

**Date:** 2026-05-11
**Source:** Codex CLI security/quality review of hive (commit `2e3bc15`)
**Status:** Draft for implementation

## Goal

Resolve all 9 findings from the codex review (2 CRITICAL, 3 HIGH, 3 MEDIUM, 1 LOW group) in a single coordinated pass that introduces shared safety primitives instead of scattering one-off fixes.

## Non-goals

- Refactor `app.py` into smaller modules. It is getting long, but that is a separate concern.
- Re-do already-shipped work: the `remain-on-exit on` / dashboard stderr log / `set_window_option` from the prior session stay as-is.
- Replace `is_pane_alive` (flagged as unused). Keep it; trivial and may be useful later.
- Add a notification framework. The single error banner described below is enough for this pass.

## Findings addressed

| # | Sev | Finding | Where addressed |
|---|---|---|---|
| 1 | CRITICAL | Shell injection via session names in `cmd = f"claude ... --name {name}"` (`app.py:91,286`, `__main__.py:53`) | §1, §2 |
| 2 | CRITICAL | tmux format injection via raw session names in `status-left` (`app.py:223` → `tmux.py:94`) | §1, §3 |
| 3 | HIGH | `poll_sessions` has no exception boundary; one exception kills the dashboard refresh loop (`app.py:103`) | §4 |
| 4 | HIGH | Hooks installed silently with bare `hive-hook` (PATH-dependent), non-atomic settings.json write (`install_hooks.py`, `app.py:55`) | §5 |
| 5 | HIGH | Non-atomic JSON writes in `HiveState.save` and `install_hooks`; deterministic temp name race in `hook_writer.py:51` | §1, §5, §6 |
| 6 | MEDIUM | Config/state load trusts shapes/types; malformed file crashes startup | §6 |
| 7 | MEDIUM | Clone path traversal: `repo_name = url.rstrip('/').split('/')[-1].removesuffix('.git')` accepts `.`/`..` | §7 |
| 8 | MEDIUM | `git clone/pull` returncode ignored; `TmuxClient.new_window` crashes on non-numeric output | §7, §8 |
| 9 | MEDIUM | Rename inconsistency between tmux window, state dict, and hook-state file | §9 |
| 10 | LOW | Dead/misleading code | §10 |

## Architecture

Five cohesive changes, sharing infrastructure introduced in §1.

### §1 — New module: `src/hive/safety.py`

Single source of truth for input safety. Each helper is small and independently testable.

```python
SESSION_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_REPLACE_RE = re.compile(r"[^A-Za-z0-9._-]")

class InvalidSessionName(ValueError): ...
class TmuxError(RuntimeError): ...

def validate_session_name(name: str) -> None:
    """Raise InvalidSessionName if not allowlisted."""

def sanitize_session_name(name: str) -> str:
    """Force a string into the allowlist. Empty/all-bad → 'session'.
       Used only when restoring legacy state, never on user input."""

def atomic_write_text(path: Path, content: str) -> None:
    """tempfile in same dir → flush → fsync → os.replace."""

def escape_tmux_format(text: str) -> str:
    """Replace '#' with '##' so tmux format strings stay literal."""
```

### §2 — Lock down command construction

Change `TmuxClient.new_window` to accept `command_args: list[str]` instead of a `command: str`. Internally we shell-quote with `shlex.join` and pass to tmux as a single shell command (tmux's `new-window <command>` does invoke a shell).

Call-site changes:

- `app.py:91,286`: build the list `["claude", "--dangerously-skip-permissions", "--name", name]`, append `["--resume", session_id]` or `["--continue"]`.
- `__main__.py:53`: same.

All call sites that pass session names through this path call `validate_session_name(name)` first; the dialogs (`SessionOptionsScreen`, `RenameScreen`) validate on submit and show an inline error if invalid.

### §3 — Tmux status bar escape

In `TmuxClient.set_status_bar(text)`, the caller (`app.py:_update_tmux_status`) builds the string. Move the `escape_tmux_format` call into the *call site* (only escape the dynamic portions — session names — not the static labels), so users still get to see literal labels containing `#` we may want later. Concretely:

```python
parts = [f"hive: {total} sessions"]
...
for s in self.session_data_map.values():
    if s.state == SessionState.WAITING:
        parts.append(f"{escape_tmux_format(s.name)} ●")
```

This is belt-and-suspenders: the allowlist already forbids `#` in names, but we don't want a regression on the allowlist to become an injection.

### §4 — Poll-loop error boundary

Wrap the `_refresh_sessions` body in `try/except Exception`. On exception:

1. Log full traceback to stderr (which we already redirect to `~/.claude/hive/dashboard.log`).
2. Set a new `Label#error-banner` (placed below the header) to a one-line summary: `"refresh failed: <type>: <msg> — see ~/.claude/hive/dashboard.log"`.
3. Continue the loop.

On the next successful refresh, clear the banner.

```python
@work(exclusive=True)
async def poll_sessions(self) -> None:
    while True:
        try:
            await self._refresh_sessions()
            self._clear_error_banner()
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            self._set_error_banner(f"refresh failed: {type(exc).__name__}: {exc}")
        await asyncio.sleep(self.config.refresh_interval_ms / 1000)
```

The banner widget is composed unconditionally; it stays empty/hidden when there is no error.

### §5 — Hook installation: explicit, absolute path, atomic

- Remove `install_hooks()` call from `HiveApp.__init__`.
- On dashboard startup, check whether the hook command in `~/.claude/settings.json` matches what we'd install. If not, show a one-line banner: `"Hooks not installed. Run 'hive install-hooks' to enable state detection."` (Distinct from the error banner above — this is informational, top of the screen.)
- The `hive install-hooks` CLI command writes the absolute command:

  ```python
  HOOK_COMMAND = f"{shlex.quote(sys.executable)} -m hive.hook_writer"
  ```

  This survives `PATH` shenanigans and works from any cwd because Python resolves `hive.hook_writer` via the installed package.
- Settings file written via `atomic_write_text`.

### §6 — Atomic writes everywhere; tolerant load

- `HiveState.save` → uses `atomic_write_text`.
- `install_hooks` → uses `atomic_write_text`.
- `hook_writer.main` → switch from `path.with_suffix(".json.tmp")` (deterministic, racy) to `tempfile.mkstemp(dir=state_dir(), prefix=f"{name}.", suffix=".json.tmp")`. Each concurrent fire gets a unique temp file, then `os.replace` to the final path.

Tolerant load:

- `HiveConfig.load` and `HiveState.load`: catch `(json.JSONDecodeError, tomllib.TOMLDecodeError, OSError, TypeError, ValueError)`, fall back to defaults, log a warning to stderr (which lands in `dashboard.log`).
- `install_hooks`: same — if `settings.json` exists but isn't a dict, or `hooks` isn't a dict, treat as empty and re-init that key (do not blow up the user's other hooks if the structure is mostly valid; only re-init the broken key).

### §7 — Clone path validation + git error surfacing

```python
def _validate_clone_target(clone_path: str, repo_name: str) -> str:
    if repo_name in {"", ".", ".."} or "/" in repo_name or "\\" in repo_name:
        raise ValueError(f"unsafe repo name: {repo_name!r}")
    base = Path(clone_path).resolve()
    target = (base / repo_name).resolve()
    if not target.is_relative_to(base):
        raise ValueError(f"target escapes clone path: {target}")
    return str(target)
```

Used in `action_clone_session` before any subprocess call. On `ValueError`, show a modal explaining the rejection and abort.

`git clone` and `git pull` calls in `action_clone_session` check `returncode`; on non-zero, show a modal with the captured stderr and abort session creation.

### §8 — Tmux subprocess error handling

`TmuxClient.new_window` becomes:

```python
def new_window(self, name: str, cwd: str, command_args: list[str], env=None) -> int:
    args = ["tmux", "new-window", "-t", self.session_name, "-n", name, "-c", cwd]
    if env:
        for k, v in env.items():
            args.extend(["-e", f"{k}={v}"])
    args.extend(["-P", "-F", "#{window_index}", shlex.join(command_args)])
    result = self._run(args, text=True)
    if result.returncode != 0:
        raise TmuxError(f"new-window failed (rc={result.returncode}): {result.stderr.strip()}")
    try:
        return int(result.stdout.strip())
    except ValueError as exc:
        raise TmuxError(f"new-window returned non-numeric output: {result.stdout!r}") from exc
```

Callers in `app.py` and `__main__.py` catch `TmuxError`, surface via modal in the app and stderr in the CLI, and roll back any partial state mutation (don't `add_session` if `new_window` failed).

### §9 — Rename: disallow on active sessions

In `action_rename_session`:

1. Check if the session is currently active (i.e., present in `self.session_data_map`, which means tmux has the window).
2. If active, show a modal: `"Cannot rename a running session. Kill it first, then rename."` and return.
3. If inactive (e.g., a state-only entry that hasn't been restored yet), allow the rename of `state.sessions` only.

This eliminates the hook-state vs dashboard-state divergence problem.

Side-fix: `TmuxClient.list_windows` parses `#{window_index}:#{window_name}:#{pane_pid}` with `split(":")`. Window names can contain `:` from external sources (we won't allow them via our flow, but they may exist). Switch the format to use a delimiter that the allowlist forbids: e.g., `#{window_index}\t#{window_name}\t#{pane_pid}` and split on `\t`. Tab is safe because tmux doesn't substitute it and the allowlist forbids it in names.

### §10 — Dead code cleanup

- `detector.py`: remove `detect_context_usage` and `CONTEXT_WINDOWS` (unused).
- `app.py`: remove `action_search` method and the `slash` binding pointing to it.
- `__main__.py`: remove unused `config` parameter from `_ensure_session`.
- `tmux.py:list_windows`: rename returned key `active` → `alive` (the value is `pane_pid > 0`, which means alive, not "currently selected"). Update `__main__.py:33` which reads `win["active"]`.

## Data flow changes

```
User input (dialog)
  → validate_session_name() → InvalidSessionName? show inline error : proceed
  → app._create_session(name, ...)
  → TmuxClient.new_window(name, cwd, ["claude", ...])  # list, not string
  → shlex.join inside new_window → tmux new-window -- '<quoted>'
  → returncode/output checked → TmuxError on failure → modal + rollback
```

```
Restore from state.json
  → HiveState.load (tolerant: bad file → empty state + warning)
  → for each session name:
       try validate_session_name
       on InvalidSessionName: name = sanitize_session_name(orig); log warning banner
  → tmux.new_window(name, ...)
```

```
Hook fires in Claude Code
  → hive-hook (now: sys.executable -m hive.hook_writer)
  → mkstemp in state_dir() → write → os.replace → final path
  → multiple concurrent fires: each gets unique temp, no collision
```

## Error handling matrix

| Failure | Old behavior | New behavior |
|---|---|---|
| Bad session name typed in dialog | accepted, breaks shell/tmux | rejected with inline error |
| Bad session name in state.json | restored, breaks shell/tmux | sanitized, warning banner |
| Malformed config.toml/state.json | crash at startup | defaults loaded, warning in log |
| Exception in `_refresh_sessions` | dashboard window dies silently | banner shown, log written, loop continues |
| `git clone` fails | session created in broken state | modal with stderr, no session created |
| `tmux new-window` fails | crash on `int(...)` | TmuxError raised, modal, no state mutation |
| Concurrent hook writers | race on `.json.tmp` (one loses) | unique temps, all succeed |
| Power loss mid-state-write | corrupt JSON, future startup crash | atomic — old or new, never partial |
| Rename of running session | hook state desyncs from dashboard | rejected with modal |

## Testing

New `tests/test_safety.py`:

- `validate_session_name`: accept `a-z`, `A-Z`, `0-9`, `.`, `_`, `-`, length 1..64; reject `""`, length 65, every category of bad char (space, `;`, `#`, `$`, `(`, `/`, `..`, unicode, control chars).
- `sanitize_session_name`: replaces forbidden chars with `_`, truncates to 64, returns `"session"` for empty/all-bad input.
- `atomic_write_text`: writes content; intermediate temp file does not survive; concurrent writers from two threads both succeed and final content is one of the two writes (never partial).
- `escape_tmux_format`: `"#"` → `"##"`, idempotency tests.

Update `tests/test_tmux.py`:

- `new_window` accepts `command_args: list[str]`, calls `tmux` with `shlex.join` of those args.
- `new_window` raises `TmuxError` on non-zero returncode.
- `new_window` raises `TmuxError` on non-numeric output.
- `list_windows` parses tab-delimited output correctly; handles a window name containing `:`.

Update `tests/test_app.py`:

- `_refresh_sessions` exception → poll loop continues, banner is set.
- Next successful `_refresh_sessions` clears the banner.
- `action_rename_session` on an active session shows the modal and does not mutate state.
- `_restore_sessions` with a bad session name in state.json sanitizes it and shows the warning banner.

Update `tests/test_install_hooks.py`:

- Hook command written is `"<sys.executable> -m hive.hook_writer"` (with `shlex.quote`).
- Settings written atomically (no `.tmp` left over).
- Malformed `settings.json` (top-level list) doesn't crash; `hooks` key is reset, other keys preserved.

Update `tests/test_hook_writer.py`:

- Two concurrent invocations both succeed (no `FileExistsError` on `.json.tmp`).
- Final state file is one of the two writes (whichever won the `os.replace` race), never partial.

Coverage target: keep at parity or slightly higher than current.

## Backwards compatibility

- Existing state files: all 3 sessions in the user's current `state.json` (`owui-nbg-001`, `DevSecOps-001`, `claude-code-sandbox-001`) pass the new allowlist. No sanitization will trigger.
- Settings file: existing installations have `hive-hook` in `settings.json`. The next time the user runs `hive install-hooks`, it gets rewritten to the absolute command. Old hooks continue to work as long as `hive-hook` is on PATH; we replace, not duplicate.
- The dashboard auto-install removal means existing users will see the "Hooks not installed" banner *only if* they've never installed hooks. Anyone who has run hive at least once before is already fine.

## Migration / rollout

Single PR. No phased rollout needed. Steps:

1. Land `safety.py` + tests first (no callers yet → safe).
2. Update `tmux.py` (signature change + TmuxError) + tests.
3. Update `app.py` and `__main__.py` call sites (this is the breaking-internal-API step; keep all changes in one commit).
4. Update `install_hooks.py` + `hook_writer.py` + tests.
5. Dead code removal in a final commit.

Each commit passes the full test suite.

## Risks

| Risk | Mitigation |
|---|---|
| `shlex.join` produces a string tmux/sh interprets differently than expected | tests cover edge cases (single quotes, dollar signs); manual smoke test before merging |
| Atomic-write helper introduces fsync latency on every save | only called on user-initiated state mutations (~few/minute), not in poll loop |
| Dropping auto-install of hooks breaks users who relied on it | one-line banner directs them to `hive install-hooks`; CLI command unchanged |
| `tempfile.mkstemp` in hook state dir leaves stragglers if process crashes between mkstemp and replace | accept this; stale `.json.tmp` files are harmless and can be cleaned up by a future janitor |

## Open questions

None — all design decisions made. If implementation reveals a concrete problem (e.g., shlex behaving unexpectedly with tmux), surface it in the PR description and decide there.
