from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
import traceback
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Label, Static

from hive.config import HiveConfig, HiveState
from hive.detector import SessionState, detect_context_pct_from_pane, detect_model, detect_state, detect_urls, probe_url
from hive.hook_state import read_session_id, read_session_state_with_meta, remove_session_state
from hive.install_hooks import hook_installed
from hive.safety import (
    InvalidSessionName,
    TmuxError,
    sanitize_session_name,
    validate_session_name,
)
from hive.tmux import TmuxClient
from hive.widgets.dialogs import (
    CloneScreen,
    ConfirmKillScreen,
    ErrorScreen,
    FolderExistsScreen,
    ProjectPickerScreen,
    RenameScreen,
    SessionOptionsScreen,
)
from hive.synopsis import get_synopsis, remove_cached_synopsis
from hive.widgets.preview import PreviewPane
from hive.widgets.session_list import SessionData, SessionListItem, SessionListView


_SPLASH_BASE = (
    "\n"
    "[#DAA520]         __    __    __    __    __[/]\n"
    "[#DAA520]        /  \\__/  \\__/  \\__/  \\__/  [/]\\\n"
    "[#DAA520]        \\__/  \\__/  \\__/  \\__/  \\__/[/]\n"
    "[#DAA520]        /  \\__/  \\__/  \\__/  \\__/  [/]\\\n"
    "[#DAA520]        \\__/  \\__/  \\__/  \\__/  \\__/[/]\n"
    "\n"
    "[bold #FFD700]        ██╗  ██╗██╗██╗   ██╗███████╗[/]\n"
    "[bold #FFD700]        ██║  ██║██║██║   ██║██╔════╝[/]\n"
    "[bold #FFD700]        ███████║██║██║   ██║█████╗[/]\n"
    "[bold #FFD700]        ██╔══██║██║╚██╗ ██╔╝██╔══╝[/]\n"
    "[bold #FFD700]        ██║  ██║██║ ╚████╔╝ ███████╗[/]\n"
    "[bold #FFD700]        ╚═╝  ╚═╝╚═╝  ╚═══╝  ╚══════╝[/]\n"
    "\n"
    "[#B8860B]         Claude Code Orchestrator[/]\n"
    "\n"
    "[#DAA520]         __    __    __    __    __[/]\n"
    "[#DAA520]        /  \\__/  \\__/  \\__/  \\__/  [/]\\\n"
    "[#DAA520]        \\__/  \\__/  \\__/  \\__/  \\__/[/]\n"
    "\n"
)

_DOT_FRAMES = (".    ", ". .  ", ". . .", " . . ", "  . .", "   . ")


def _build_splash_text(frame: int) -> str:
    dots = _DOT_FRAMES[frame % len(_DOT_FRAMES)]
    return _SPLASH_BASE + f"[dim]              warming up  {dots}[/]"


SPLASH_ART = _build_splash_text(0)


def _validate_clone_target(clone_path: str, repo_name: str) -> str:
    if repo_name in {"", ".", ".."} or "/" in repo_name or "\\" in repo_name:
        raise ValueError(f"unsafe repo name: {repo_name!r}")
    base = Path(clone_path).resolve()
    target = (base / repo_name).resolve()
    if not target.is_relative_to(base):
        raise ValueError(f"target escapes clone path: {target}")
    return str(target)


_ALLOWED_URL_SCHEMES = ("https://", "http://", "ssh://", "git://")
_SCP_URL_RE = re.compile(r"^[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+:[^\s]+$")


def _validate_clone_url(url: str) -> str:
    if not isinstance(url, str):
        raise ValueError("url must be a string")
    if any(c in url for c in "\r\n\t\x00"):
        raise ValueError("url contains control characters")
    stripped = url.strip()
    if not stripped:
        raise ValueError("url is empty")
    if stripped.startswith("-"):
        raise ValueError("url may not start with '-'")
    if any(stripped.lower().startswith(s) for s in _ALLOWED_URL_SCHEMES):
        return stripped
    if _SCP_URL_RE.match(stripped):
        return stripped
    raise ValueError(f"unsupported url: {stripped!r}")


def _session_sort_key(s) -> tuple:
    is_waiting = s.state == SessionState.WAITING
    # Waiting sessions first; within waiting, oldest updated_at first.
    # Empty/missing waiting_since sorts last among waiting sessions.
    waiting_since = s.waiting_since or "\uffff"
    return (not is_waiting, waiting_since if is_waiting else "", s.name)


_CRED_URL_RE = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*://)[^/\s@]+@")


def _redact_git_output(text: str) -> str:
    if not text:
        return text
    return _CRED_URL_RE.sub(r"\1REDACTED@", text)


class HiveApp(App):
    CSS_PATH = "hive.tcss"

    BINDINGS = [
        Binding("Q", "quit_app", "Quit all", show=False, key_display="Q"),
        Binding("n", "new_session", "New", show=False),
        Binding("f", "free_session", "Free", show=False),
        Binding("g", "clone_session", "Clone", show=False),
        Binding("k", "kill_session", "Kill", show=False),
        Binding("r", "resume_session", "Resume", show=False),
        Binding("R", "rename_session", "Rename", show=False, key_display="R"),
        Binding("u", "open_url", "URL", show=False),
        Binding("enter", "attach_session", "Attach", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.config = HiveConfig.load_default()
        self.state = HiveState.load_default()
        self.tmux = TmuxClient(self.config.tmux_session_name)
        self.session_data_map: dict[str, SessionData] = {}
        self._last_attached: str | None = None
        self._url_cache: dict[int, tuple[int, list[tuple[str, bool]]]] = {}
        self._prev_active_window: int | None = None
        self._starting_up: bool = True
        self._orphan_strikes: dict[str, int] = {}
        self._synopsis_in_flight: set[str] = set()

    def compose(self) -> ComposeResult:
        will_restore = bool(self.state.sessions)
        yield Label(self._build_header_text(), id="header")
        yield Label("", id="info-banner", classes="banner banner-info")
        yield Label("", id="warning-banner", classes="banner banner-warn")
        yield Label("", id="error-banner", classes="banner banner-error")
        # Splash is mounted visible iff we have sessions to restore. Main is
        # hidden in that same case so it doesn't fight for layout space.
        yield Static(
            SPLASH_ART,
            id="splash",
            classes="" if will_restore else "hidden",
        )
        with Horizontal(id="main", classes="hidden" if will_restore else ""):
            yield SessionListView(id="session-panel", initial_index=0)
            yield PreviewPane("(no session selected)", id="preview-panel")
        yield Label(
            "n:new  f:free  g:clone  k:kill  R:rename  u:url  ↵:attach  Q:quit",
            id="footer-bar",
        )

    async def on_mount(self) -> None:
        # Return quickly so Textual finishes its initial paint and the splash
        # actually shows. All restore + warmup work runs in a follow-up task.
        self.query_one("#session-panel", SessionListView).focus()
        if self.state.sessions:
            self.run_worker(self._startup_flow(), exclusive=False)
        else:
            self._after_startup()

    async def _startup_flow(self) -> None:
        spawned = await asyncio.to_thread(self._restore_sessions)
        self._show_splash(False)
        self._after_startup()
        if spawned:
            await self._wait_and_purge(spawned)

    def _after_startup(self) -> None:
        shortcuts = "F1:dashboard  Ctrl-b n/p:next/prev  Shift+drag:select  Ctrl+Shift+C:copy"
        self.tmux.setup_shortcut_bar(shortcuts)
        if not hook_installed():
            from hive.install_hooks import install_hooks
            if not install_hooks():
                self._set_info_banner("Could not auto-install hooks. Run 'hive install-hooks' manually.")
        self._starting_up = False
        self.poll_sessions()

    def _show_splash(self, visible: bool) -> None:
        try:
            splash = self.query_one("#splash", Static)
            main = self.query_one("#main", Horizontal)
        except Exception:
            return
        # Use Textual's display property (overrides CSS) and clear the initial
        # .hidden class so the toggle is unambiguous.
        splash.set_class(False, "hidden")
        splash.display = visible
        main.display = not visible
        splash.refresh(layout=True)
        main.refresh(layout=True)

    async def _wait_and_purge(self, spawned: list[str], max_wait: float = 15.0) -> None:
        """Wait in the background for spawned sessions to finish bootstrapping,
        then purge any that died. The dashboard is already visible and
        sessions appear via normal polling as they become ready."""
        loop = asyncio.get_event_loop()
        end = loop.time() + max_wait
        while loop.time() < end:
            pending = await asyncio.to_thread(self._pending_spawned, spawned)
            if not pending:
                break
            await asyncio.sleep(1.0)
        purged = self._purge_dead_sessions(spawned)
        if purged:
            preview = ", ".join(purged[:3])
            more = f" (+{len(purged) - 3} more)" if len(purged) > 3 else ""
            self._set_info_banner(
                f"Removed {len(purged)} stale session(s) that could not resume: {preview}{more}"
            )

    def _pending_spawned(self, spawned: list[str]) -> list[str]:
        windows = self.tmux.list_windows()
        alive = {w["name"]: w["index"] for w in windows if w["index"] != 0}
        pending: list[str] = []
        for name in spawned:
            idx = alive.get(name)
            if idx is None:
                continue
            hook_state, _ = read_session_state_with_meta(name)
            if hook_state is not None and hook_state != SessionState.BOOTSTRAPPING:
                continue
            if hook_state is None:
                pane_text = self.tmux.capture_pane(idx)
                if detect_state(pane_text) != SessionState.BOOTSTRAPPING:
                    continue
            pending.append(name)
        return pending

    def _set_error_banner(self, text: str) -> None:
        try:
            label = self.query_one("#error-banner", Label)
            label.update(text)
            label.set_class(bool(text), "has-text")
        except Exception:
            pass

    def _clear_error_banner(self) -> None:
        try:
            label = self.query_one("#error-banner", Label)
            label.update("")
            label.set_class(False, "has-text")
        except Exception:
            pass

    def _set_warning_banner(self, text: str) -> None:
        try:
            label = self.query_one("#warning-banner", Label)
            label.update(text)
            label.set_class(bool(text), "has-text")
        except Exception:
            pass

    def _set_info_banner(self, text: str) -> None:
        try:
            label = self.query_one("#info-banner", Label)
            label.update(text)
            label.set_class(bool(text), "has-text")
        except Exception:
            pass

    def _restore_sessions(self) -> list[str]:
        """Spawn tmux windows for saved sessions. Returns the list of names
        that were (re)spawned this call so the caller can later verify which
        actually survived."""
        if not self.state.sessions:
            return []

        renames: list[tuple[str, str]] = []
        for name in list(self.state.sessions.keys()):
            try:
                validate_session_name(name)
            except InvalidSessionName:
                base = sanitize_session_name(name)
                clean = base
                counter = 1
                while clean in self.state.sessions and clean != name:
                    suffix = f"_{counter}"
                    truncated_base = base[: 64 - len(suffix)]
                    clean = sanitize_session_name(truncated_base + suffix)
                    counter += 1
                    if counter > 1000:
                        # Give up; overwrite would be worse than dropping.
                        clean = base
                        break
                self.state.sessions[clean] = self.state.sessions.pop(name)
                renames.append((name, clean))
        if renames:
            summary = ", ".join(f"{a!r}->{b!r}" for a, b in renames)
            self._set_warning_banner(f"sanitized state names: {summary}")

        windows = self.tmux.list_windows()
        existing_names = {w["name"] for w in windows if w["index"] != 0}
        spawned: list[str] = []
        for name, info in list(self.state.sessions.items()):
            if name in existing_names:
                continue
            project_path = info.get("project_path", "")
            if not os.path.isdir(project_path):
                self.state.remove_session(name)
                continue
            session_id = info.get("claude_session_id", "")
            if session_id:
                args = self._build_claude_args(name, session_id)
            else:
                args = [
                    "claude", "--dangerously-skip-permissions",
                    "--name", name, "--continue",
                ]
            # Drop any stale hook state from the previous run so the freshly
            # spawned claude isn't reported in its old state until its own
            # hooks fire.
            remove_session_state(name)
            try:
                window_idx = self.tmux.new_window(
                    name, project_path, args,
                    env={"HIVE_SESSION": name},
                    detached=True,
                )
            except TmuxError as exc:
                import sys
                print(f"hive: failed to restore session {name!r}: {exc}", file=sys.stderr)
                continue
            self.state.sessions[name]["tmux_window"] = window_idx
            spawned.append(name)
        self.state.save_default()
        return spawned

    def _purge_dead_sessions(self, attempted: list[str]) -> list[str]:
        """Drop entries whose tmux window vanished shortly after spawn — i.e.,
        claude exited because --continue could not resume the session. Keeps
        state.json honest so we don't keep trying to revive ghosts."""
        if not attempted:
            return []
        windows = self.tmux.list_windows()
        surviving = {w["name"] for w in windows if w["index"] != 0}
        purged: list[str] = []
        for name in attempted:
            if name in surviving:
                continue
            self.state.remove_session(name)
            remove_session_state(name)
            purged.append(name)
        if purged:
            self.state.save_default()
        return purged

    # Number of consecutive polls a state.json entry can be missing from
    # tmux before we drop it. With the default 5s poll interval that's ~10s.
    ORPHAN_STRIKE_THRESHOLD = 2

    def _purge_orphans_in_state(self, current_names: set[str]) -> None:
        """Drop state.json entries whose tmux window has been missing across
        several consecutive polls. Catches sessions that die slowly (after the
        startup warmup) or are killed outside the dashboard."""
        dirty = False
        for name in list(self.state.sessions.keys()):
            if name in current_names:
                self._orphan_strikes.pop(name, None)
                continue
            strikes = self._orphan_strikes.get(name, 0) + 1
            if strikes >= self.ORPHAN_STRIKE_THRESHOLD:
                self.state.remove_session(name)
                remove_session_state(name)
                self._orphan_strikes.pop(name, None)
                dirty = True
            else:
                self._orphan_strikes[name] = strikes
        if dirty:
            self.state.save_default()

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

    async def _refresh_sessions(self) -> None:
        windows = self.tmux.list_windows()
        list_view = self.query_one("#session-panel", SessionListView)
        preview = self.query_one("#preview-panel", PreviewPane)

        current_names = set()
        for win in windows:
            if win["index"] == 0:
                continue
            name = win["name"]
            current_names.add(name)

            pane_text = self.tmux.capture_pane(win["index"])
            hook_state, hook_updated_at = read_session_state_with_meta(name)
            state = hook_state if hook_state is not None else detect_state(pane_text)
            waiting_since = hook_updated_at if state == SessionState.WAITING else None

            model, context_str = detect_model(pane_text)

            scrollback = self.tmux.capture_pane_scrollback(win["index"])
            scrollback_hash = hash(scrollback)
            cached = self._url_cache.get(win["index"])
            if cached is not None and cached[0] == scrollback_hash:
                urls = cached[1]
            else:
                raw_urls = detect_urls(scrollback)
                urls = [(u, probe_url(u)) for u in raw_urls[:5]]
                self._url_cache[win["index"]] = (scrollback_hash, urls)

            project_path = self.state.sessions.get(name, {}).get("project_path", "~")

            context_pct = detect_context_pct_from_pane(pane_text)

            data = SessionData(
                name=name,
                project_path=project_path,
                tmux_window=win["index"],
                state=state,
                model=model,
                context_str=context_str,
                context_pct=context_pct,
                urls=urls,
                synopsis=self.session_data_map.get(name, SessionData(name=name, project_path="", tmux_window=0)).synopsis,
                waiting_since=waiting_since,
            )
            self.session_data_map[name] = data

            session_id = read_session_id(name)
            if session_id and name not in self._synopsis_in_flight:
                self._synopsis_in_flight.add(name)
                self._fetch_synopsis(name, project_path, session_id)

        removed = set(self.session_data_map.keys()) - current_names
        for name in removed:
            del self.session_data_map[name]
        live_indices = {win["index"] for win in windows}
        for idx in list(self._url_cache.keys()):
            if idx not in live_indices:
                del self._url_cache[idx]

        self._purge_orphans_in_state(current_names)

        self._rebuild_list(list_view)
        self._sync_dashboard_focus(list_view, windows)
        self._ensure_highlight(list_view)
        self._update_preview(list_view, preview)
        self._update_header()

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

    def _sync_dashboard_focus(self, list_view: SessionListView, windows: list[dict]) -> None:
        active_idx: int | None = None
        last_active_name: str | None = None
        for win in windows:
            if win.get("active"):
                active_idx = win["index"]
            if win.get("last_active") and win["index"] != 0:
                last_active_name = win["name"]

        prev = self._prev_active_window
        self._prev_active_window = active_idx

        if active_idx != 0:
            return
        if prev is None or prev == 0:
            return

        if last_active_name is not None:
            for i, item in enumerate(list_view.children):
                if isinstance(item, SessionListItem) and item.data.name == last_active_name:
                    list_view.index = i
                    break
        list_view.focus()

    def _ensure_highlight(self, list_view: SessionListView) -> None:
        children = list(list_view.children)
        if not children:
            return
        if list_view.index is None or list_view.index < 0 or list_view.index >= len(children):
            list_view.index = 0
        list_view.focus()

    def _rebuild_list(self, list_view: SessionListView) -> None:
        ready = [s for s in self.session_data_map.values() if s.state != SessionState.BOOTSTRAPPING]
        sorted_sessions = sorted(ready, key=_session_sort_key)
        new_names = [s.name for s in sorted_sessions]

        existing: dict[str, SessionListItem] = {}
        for item in list_view.children:
            if isinstance(item, SessionListItem):
                existing[item.data.name] = item

        old_names = list(existing.keys())

        if new_names == old_names:
            for item in existing.values():
                updated = self.session_data_map.get(item.data.name)
                if updated:
                    item.data = updated
                    item.refresh_content()
            return

        target_name = self._last_attached
        if not target_name:
            highlighted_item = list_view.highlighted_child
            if isinstance(highlighted_item, SessionListItem):
                target_name = highlighted_item.data.name

        list_view.clear()
        restore_index = 0
        for i, data in enumerate(sorted_sessions):
            list_view.append(SessionListItem(data))
            if data.name == target_name:
                restore_index = i
        if sorted_sessions:
            list_view.index = restore_index
            list_view.focus()

    def _update_preview(self, list_view: SessionListView, preview: PreviewPane) -> None:
        data = list_view.get_session_data()
        if data:
            preview.set_content(data.synopsis or "(generating synopsis...)")
        else:
            preview.clear_content()

    def _build_header_text(self) -> str:
        brand = "[bold yellow]🐝[/] [black on bright_yellow] H I V E [/] [yellow]⬢⬡⬢[/]"
        title = "[bold bright_cyan]Claude Code Orchestrator[/]"
        if self._starting_up:
            return "  [dim]│[/]  ".join([brand, title, "[italic dim]starting up…[/]"])
        booting = sum(1 for s in self.session_data_map.values() if s.state == SessionState.BOOTSTRAPPING)
        # Count only sessions that have actually loaded — matches what's rendered in the list
        # (BOOTSTRAPPING sessions are filtered out of the panel by _rebuild_list).
        ready = len(self.session_data_map) - booting
        waiting = sum(1 for s in self.session_data_map.values() if s.state == SessionState.WAITING)
        parts = [brand, title, f"[bold]{ready}[/] [dim]sessions[/]"]
        if waiting:
            parts.append(f"[bold black on bright_yellow] {waiting} Waiting [/]")
        if booting:
            parts.append(f"[bold black on bright_blue] {booting} Loading [/]")
        return "  [dim]│[/]  ".join(parts)

    def _update_header(self) -> None:
        self.query_one("#header", Label).update(self._build_header_text())

    def on_list_view_highlighted(self, event: SessionListView.Highlighted) -> None:
        preview = self.query_one("#preview-panel", PreviewPane)
        list_view = self.query_one("#session-panel", SessionListView)
        self._update_preview(list_view, preview)

    def on_list_view_selected(self, event: SessionListView.Selected) -> None:
        item = event.item
        if isinstance(item, SessionListItem):
            self._last_attached = item.data.name
            self.tmux.select_window(item.data.tmux_window)

    def _scan_projects(self) -> list[dict]:
        projects: dict[str, dict] = {}
        for scan_path in self.config.scan_paths:
            expanded = os.path.expanduser(scan_path)
            if not os.path.isdir(expanded):
                continue
            for entry in sorted(os.listdir(expanded)):
                full = os.path.join(expanded, entry)
                if os.path.isdir(full) and not entry.startswith("."):
                    if full not in projects:
                        last_used = self.state.projects.get(full, {}).get("last_used", "")
                        projects[full] = {"name": entry, "path": full, "age": self._format_age(last_used)}
        return list(projects.values())

    def _format_age(self, iso_str: str) -> str:
        if not iso_str:
            return ""
        try:
            dt = datetime.fromisoformat(iso_str)
            diff = datetime.now(timezone.utc) - dt
            if diff.days > 0:
                return f"{diff.days}d"
            hours = diff.seconds // 3600
            if hours > 0:
                return f"{hours}h"
            minutes = diff.seconds // 60
            return f"{minutes}m"
        except (ValueError, TypeError):
            return ""

    def _next_session_name(self, project_name: str) -> str:
        i = 1
        while f"{project_name}-{i:03d}" in self.session_data_map:
            i += 1
        return f"{project_name}-{i:03d}"

    def _build_claude_args(
        self, session_name: str, resume_id: str | None
    ) -> list[str]:
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
        try:
            window_idx = self.tmux.new_window(
                session_name, project_path, args, env={"HIVE_SESSION": session_name}
            )
        except TmuxError as exc:
            await self.push_screen_wait(ErrorScreen("tmux failed", str(exc)))
            return
        self.state.add_session(session_name, project_path, window_idx, resume_id or "")
        self.state.save_default()

    @work
    async def action_new_session(self) -> None:
        projects = self._scan_projects()
        result = await self.push_screen_wait(ProjectPickerScreen(projects))
        if result is None:
            return
        project_path = result["path"]
        project_name = result["name"]
        options = await self.push_screen_wait(
            SessionOptionsScreen(project_name, project_path)
        )
        if options is None:
            return
        name = options["name"] or self._next_session_name(project_name)
        await self._create_session(project_path, name, options.get("resume_session_id"))

    @work
    async def action_free_session(self) -> None:
        home = str(Path.home())
        options = await self.push_screen_wait(SessionOptionsScreen("home (~)", home))
        if options is None:
            return
        name = options["name"] or self._next_session_name("free")
        await self._create_session(home, name, options.get("resume_session_id"))

    @work
    async def action_clone_session(self) -> None:
        result = await self.push_screen_wait(CloneScreen(self.config.clone_path))
        if result is None:
            return
        clone_path = result["clone_path"]
        try:
            url = _validate_clone_url(result["url"])
        except ValueError as exc:
            await self.push_screen_wait(ErrorScreen("Clone refused", str(exc)))
            return
        repo_name = url.rstrip("/").split("/")[-1].removesuffix(".git")
        if ":" in repo_name and "/" not in repo_name:
            repo_name = repo_name.split(":")[-1]
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
                    await self.push_screen_wait(
                        ErrorScreen("git pull failed", _redact_git_output(rc.stderr.strip()))
                    )
                    return
        else:
            rc = subprocess.run(
                ["git", "clone", "--", url, target], capture_output=True, text=True
            )
            if rc.returncode != 0:
                await self.push_screen_wait(
                    ErrorScreen("git clone failed", _redact_git_output(rc.stderr.strip()))
                )
                return

        name = self._next_session_name(repo_name)
        await self._create_session(target, name)

    @work
    async def action_kill_session(self) -> None:
        list_view = self.query_one("#session-panel", SessionListView)
        data = list_view.get_session_data()
        if data is None:
            return
        confirmed = await self.push_screen_wait(ConfirmKillScreen(data.name))
        if confirmed:
            self.tmux.kill_window(data.tmux_window)
            self.state.remove_session(data.name)
            self.state.save_default()
            remove_session_state(data.name)
            remove_cached_synopsis(data.name)

    async def action_resume_session(self) -> None:
        list_view = self.query_one("#session-panel", SessionListView)
        data = list_view.get_session_data()
        if data is None:
            return
        self.tmux.kill_window(data.tmux_window)
        session_id = self.state.sessions.get(data.name, {}).get("claude_session_id", "")
        await self._create_session(data.project_path, data.name, resume_id=session_id or None)

    @work
    async def action_rename_session(self) -> None:
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

    @work
    async def action_open_url(self) -> None:
        list_view = self.query_one("#session-panel", SessionListView)
        data = list_view.get_session_data()
        if data is None or not data.urls:
            return
        if len(data.urls) == 1:
            webbrowser.open(f"http://{data.urls[0][0]}")
        else:
            from hive.widgets.dialogs import UrlPickerScreen
            chosen = await self.push_screen_wait(UrlPickerScreen(data.urls))
            if chosen:
                webbrowser.open(f"http://{chosen}")

    async def action_attach_session(self) -> None:
        list_view = self.query_one("#session-panel", SessionListView)
        data = list_view.get_session_data()
        if data:
            self._last_attached = data.name
            self.tmux.select_window(data.tmux_window)

    async def action_quit_app(self) -> None:
        self.tmux.kill_session()
        self.exit()

