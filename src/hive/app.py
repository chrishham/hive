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
from textual.widgets import Label

from hive.config import HiveConfig, HiveState
from hive.detector import SessionState, detect_context_pct_from_pane, detect_model, detect_state, detect_urls, probe_url
from hive.hook_state import read_session_state, remove_session_state
from hive.install_hooks import hook_installed
from hive.safety import (
    InvalidSessionName,
    TmuxError,
    escape_tmux_format,
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
from hive.widgets.preview import PreviewPane
from hive.widgets.session_list import SessionData, SessionListItem, SessionListView


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

    def compose(self) -> ComposeResult:
        waiting = sum(1 for s in self.session_data_map.values() if s.state == SessionState.WAITING)
        total = len(self.session_data_map)
        header_text = f"hive — Claude Code Orchestrator    {total} sessions"
        if waiting:
            header_text += f" ({waiting}●)"
        yield Label(header_text, id="header")
        yield Label("", id="info-banner", classes="banner banner-info")
        yield Label("", id="warning-banner", classes="banner banner-warn")
        yield Label("", id="error-banner", classes="banner banner-error")
        with Horizontal(id="main"):
            yield SessionListView(id="session-panel", initial_index=0)
            yield PreviewPane("(no session selected)", id="preview-panel")
        yield Label(
            "n:new  f:free  g:clone  k:kill  R:rename  u:url  ↵:attach  Q:quit",
            id="footer-bar",
        )

    async def on_mount(self) -> None:
        self.query_one("#session-panel", SessionListView).focus()
        if not hook_installed():
            self._set_info_banner("Hooks not installed. Run 'hive install-hooks' to enable state detection.")
        self._restore_sessions()
        self.poll_sessions()

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

    def _restore_sessions(self) -> None:
        if not self.state.sessions:
            return

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
            try:
                window_idx = self.tmux.new_window(
                    name, project_path, args, env={"HIVE_SESSION": name}
                )
            except TmuxError as exc:
                import sys
                print(f"hive: failed to restore session {name!r}: {exc}", file=sys.stderr)
                continue
            self.state.sessions[name]["tmux_window"] = window_idx
        self.tmux.select_window(0)
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
            hook_state = read_session_state(name)
            state = hook_state if hook_state is not None else detect_state(pane_text)

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
                preview_text=pane_text,
            )
            self.session_data_map[name] = data

        removed = set(self.session_data_map.keys()) - current_names
        for name in removed:
            del self.session_data_map[name]
        live_indices = {win["index"] for win in windows}
        for idx in list(self._url_cache.keys()):
            if idx not in live_indices:
                del self._url_cache[idx]

        self._rebuild_list(list_view)
        self._ensure_highlight(list_view)
        self._update_preview(list_view, preview)
        self._update_header()
        self._update_tmux_status()

    def _ensure_highlight(self, list_view: SessionListView) -> None:
        children = list(list_view.children)
        if not children:
            return
        if list_view.index is None or list_view.index < 0 or list_view.index >= len(children):
            list_view.index = 0
        list_view.focus()

    def _rebuild_list(self, list_view: SessionListView) -> None:
        ready = [s for s in self.session_data_map.values() if s.state != SessionState.BOOTSTRAPPING]
        sorted_sessions = sorted(
            ready,
            key=lambda s: (s.state != SessionState.WAITING, s.name),
        )
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
            preview.set_content(data.preview_text, self.config.preview_lines)
        else:
            preview.clear_content()

    def _update_header(self) -> None:
        total = len(self.session_data_map)
        waiting = sum(1 for s in self.session_data_map.values() if s.state == SessionState.WAITING)
        booting = sum(1 for s in self.session_data_map.values() if s.state == SessionState.BOOTSTRAPPING)
        header_text = f"hive — Claude Code Orchestrator    {total} sessions"
        if waiting:
            header_text += f" ({waiting}●)"
        if booting:
            header_text += f"  loading {booting}..."
        self.query_one("#header", Label).update(header_text)

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
        windows = self.tmux.list_windows()
        for win in windows:
            if win["index"] == 0:
                continue
            self.tmux.send_keys(win["index"], "q")
        import asyncio
        await asyncio.sleep(2)
        self.tmux.kill_session()
        self.exit()
