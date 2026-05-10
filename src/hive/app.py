from __future__ import annotations

import asyncio
import os
import subprocess
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Label

from hive.config import HiveConfig, HiveState
from hive.detector import SessionState, detect_model, detect_state, detect_urls, probe_url
from hive.tmux import TmuxClient
from hive.widgets.dialogs import (
    CloneScreen,
    ConfirmKillScreen,
    FolderExistsScreen,
    ProjectPickerScreen,
    RenameScreen,
    SessionOptionsScreen,
)
from hive.widgets.preview import PreviewPane
from hive.widgets.session_list import SessionData, SessionListItem, SessionListView


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
        Binding("slash", "search", "Search", show=False),
        Binding("enter", "attach_session", "Attach", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.config = HiveConfig.load_default()
        self.state = HiveState.load_default()
        self.tmux = TmuxClient(self.config.tmux_session_name)
        self.session_data_map: dict[str, SessionData] = {}
        self._last_attached: str | None = None

    def compose(self) -> ComposeResult:
        waiting = sum(1 for s in self.session_data_map.values() if s.state == SessionState.WAITING)
        total = len(self.session_data_map)
        header_text = f"hive — Claude Code Orchestrator    {total} sessions"
        if waiting:
            header_text += f" ({waiting}●)"
        yield Label(header_text, id="header")
        with Horizontal(id="main"):
            yield SessionListView(id="session-panel", initial_index=0)
            yield PreviewPane("(no session selected)", id="preview-panel")
        yield Label(
            "n:new  f:free  g:clone  k:kill  R:rename  u:url  /:search  ↵:attach  Q:quit",
            id="footer-bar",
        )

    async def on_mount(self) -> None:
        self.query_one("#session-panel", SessionListView).focus()
        self._restore_sessions()
        self.poll_sessions()

    def _restore_sessions(self) -> None:
        if not self.state.sessions:
            return
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
            cmd = f"claude --name {name}"
            if session_id:
                cmd += f" --resume {session_id}"
            else:
                cmd += " --continue"
            window_idx = self.tmux.new_window(name, project_path, cmd)
            self.state.sessions[name]["tmux_window"] = window_idx
        self.state.save_default()

    @work(exclusive=True)
    async def poll_sessions(self) -> None:
        while True:
            await self._refresh_sessions()
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
            state = detect_state(pane_text)

            model, context_str = detect_model(pane_text)

            scrollback = self.tmux.capture_pane_scrollback(win["index"])
            raw_urls = detect_urls(scrollback)
            urls = [(u, probe_url(u)) for u in raw_urls[:5]]

            project_path = self.state.sessions.get(name, {}).get("project_path", "~")

            data = SessionData(
                name=name,
                project_path=project_path,
                tmux_window=win["index"],
                state=state,
                model=model,
                context_str=context_str,
                urls=urls,
                preview_text=pane_text,
            )
            self.session_data_map[name] = data

        removed = set(self.session_data_map.keys()) - current_names
        for name in removed:
            del self.session_data_map[name]

        self._rebuild_list(list_view)
        self._update_preview(list_view, preview)
        self._update_header()
        self._update_tmux_status()

    def _rebuild_list(self, list_view: SessionListView) -> None:
        sorted_sessions = sorted(
            self.session_data_map.values(),
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
        header_text = f"hive — Claude Code Orchestrator    {total} sessions"
        if waiting:
            header_text += f" ({waiting}●)"
        self.query_one("#header", Label).update(header_text)

    def _update_tmux_status(self) -> None:
        total = len(self.session_data_map)
        waiting = sum(1 for s in self.session_data_map.values() if s.state == SessionState.WAITING)
        parts = [f"hive: {total} sessions"]
        if waiting:
            parts.append(f"● {waiting} waiting")
        for s in self.session_data_map.values():
            if s.state == SessionState.WAITING:
                parts.append(f"{s.name} ●")
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

    async def _create_session(self, project_path: str, session_name: str, resume_id: str | None = None) -> None:
        cmd = f"claude --name {session_name}"
        if resume_id:
            cmd += f" --resume {resume_id}"
        window_idx = self.tmux.new_window(session_name, project_path, cmd)
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
        url = result["url"]
        clone_path = result["clone_path"]
        repo_name = url.rstrip("/").split("/")[-1].removesuffix(".git")
        target = os.path.join(clone_path, repo_name)

        if os.path.exists(target):
            choice = await self.push_screen_wait(FolderExistsScreen(target))
            if choice is None:
                return
            if choice == "pull":
                subprocess.run(["git", "-C", target, "pull"], capture_output=True)
        else:
            subprocess.run(["git", "clone", url, target], capture_output=True)

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
        new_name = await self.push_screen_wait(RenameScreen(data.name))
        if new_name and new_name != data.name:
            self.tmux.rename_window(data.tmux_window, new_name)
            if data.name in self.state.sessions:
                self.state.sessions[new_name] = self.state.sessions.pop(data.name)
            self.state.save_default()

    async def action_open_url(self) -> None:
        list_view = self.query_one("#session-panel", SessionListView)
        data = list_view.get_session_data()
        if data is None or not data.urls:
            return
        first_live = next((u for u, alive in data.urls if alive), None)
        if first_live:
            webbrowser.open(f"http://{first_live}")

    async def action_attach_session(self) -> None:
        list_view = self.query_one("#session-panel", SessionListView)
        data = list_view.get_session_data()
        if data:
            self._last_attached = data.name
            self.tmux.select_window(data.tmux_window)

    async def action_search(self) -> None:
        pass

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
