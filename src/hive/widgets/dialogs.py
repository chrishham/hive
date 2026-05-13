from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, ListView, ListItem, RadioButton, RadioSet, Static

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"


def scan_resumable_sessions(project_path: str) -> list[dict]:
    encoded = project_path.replace("/", "-")
    proj_dir = CLAUDE_PROJECTS_DIR / encoded
    if not proj_dir.is_dir():
        return []
    sessions = []
    for f in sorted(proj_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
        sid = f.stem
        mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        first_msg = _extract_first_message(f)
        sessions.append({"id": sid, "date": mtime, "summary": first_msg})
    return sessions[:10]


def _extract_first_message(path: Path) -> str:
    try:
        with open(path) as fp:
            for line in fp:
                try:
                    d = json.loads(line)
                    if d.get("type") != "user":
                        continue
                    msg = d.get("message", {})
                    if not isinstance(msg, dict):
                        continue
                    content = msg.get("content", "")
                    if isinstance(content, list):
                        for c in content:
                            if isinstance(c, dict) and c.get("type") == "text":
                                return c["text"][:60].replace("\n", " ")
                    elif isinstance(content, str):
                        return content[:60].replace("\n", " ")
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return "(no messages)"


class _KeyboardModalScreen(ModalScreen):
    def on_click(self, event) -> None:
        event.prevent_default()
        event.stop()

    def on_mouse_move(self, event) -> None:
        event.prevent_default()
        event.stop()

    def on_mouse_down(self, event) -> None:
        event.prevent_default()
        event.stop()

    def on_mouse_up(self, event) -> None:
        event.prevent_default()
        event.stop()


class ProjectPickerScreen(_KeyboardModalScreen):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, projects: list[dict]) -> None:
        super().__init__()
        self.projects = projects
        self._filtered: list[dict] = list(projects)
        self._pick_index: int = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-dialog"):
            yield Label("Select a project:", id="picker-title")
            yield Input(placeholder="Type to filter...", id="picker-filter")
            yield Static("", id="picker-list", markup=True)

    def on_mount(self) -> None:
        self._render_list()

    def _render_list(self) -> None:
        lines: list[str] = []
        for i, p in enumerate(self._filtered):
            if i == self._pick_index:
                lines.append(f"[bold white on #1a5fb4] {p['name']} [/]")
            else:
                lines.append(f" {p['name']}")
        self.query_one("#picker-list", Static).update("\n".join(lines))

    def on_key(self, event) -> None:
        if event.key == "up":
            if self._filtered and self._pick_index > 0:
                self._pick_index -= 1
                self._render_list()
            event.prevent_default()
            event.stop()
        elif event.key == "down":
            if self._filtered and self._pick_index < len(self._filtered) - 1:
                self._pick_index += 1
                self._render_list()
            event.prevent_default()
            event.stop()
        elif event.key == "enter":
            if self._filtered and 0 <= self._pick_index < len(self._filtered):
                p = self._filtered[self._pick_index]
                self.dismiss({"path": p["path"], "name": p["name"]})
            event.prevent_default()
            event.stop()

    def on_input_changed(self, event: Input.Changed) -> None:
        query = event.value.lower()
        self._filtered = [p for p in self.projects if query in p["name"].lower()]
        self._pick_index = 0
        self._render_list()

    def action_cancel(self) -> None:
        self.dismiss(None)


class SessionOptionsScreen(_KeyboardModalScreen):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, project_name: str, project_path: str) -> None:
        super().__init__()
        self.project_name = project_name
        self.project_path = project_path
        self.resumable = scan_resumable_sessions(project_path)

    def compose(self) -> ComposeResult:
        with Vertical(id="options-dialog"):
            yield Label(f"Session in {self.project_name}", id="options-title")
            items = [ListItem(Label("  New session"), name="__new__")]
            for s in self.resumable:
                items.append(ListItem(
                    Label(f"  {s['date']}  {s['summary']}"),
                    name=s["id"],
                ))
            yield ListView(*items, id="session-picker", initial_index=0)
            yield Input(placeholder="Session name (blank for auto)", id="session-name")
            with Horizontal(id="options-buttons"):
                yield Button("Create", variant="primary", id="btn-create")
                yield Button("Cancel", id="btn-cancel")
            yield Label("  d: delete selected session", id="options-hint")

    def on_key(self, event) -> None:
        lv = self.query_one("#session-picker", ListView)
        if event.key == "up":
            lv.action_cursor_up()
            event.prevent_default()
            event.stop()
        elif event.key == "down":
            lv.action_cursor_down()
            event.prevent_default()
            event.stop()
        elif event.key == "d":
            self._delete_selected(lv)
            event.prevent_default()
            event.stop()
        elif event.key == "enter":
            self._submit()
            event.prevent_default()
            event.stop()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-create":
            self._submit()
        else:
            self.dismiss(None)

    def _delete_selected(self, lv: ListView) -> None:
        item = lv.highlighted_child
        if not isinstance(item, ListItem) or item.name == "__new__":
            return
        idx = lv.index
        session_id = item.name
        if "/" in session_id or "\\" in session_id or session_id in {".", ".."}:
            return
        encoded = self.project_path.replace("/", "-")
        project_dir = CLAUDE_PROJECTS_DIR / encoded
        if project_dir.is_symlink() or not project_dir.is_dir():
            return
        try:
            resolved_dir = project_dir.resolve(strict=True)
            base = CLAUDE_PROJECTS_DIR.resolve()
        except OSError:
            return
        if not resolved_dir.is_relative_to(base):
            return
        jsonl_path = project_dir / f"{session_id}.jsonl"
        if jsonl_path.is_symlink():
            return
        if jsonl_path.is_file():
            jsonl_path.unlink()
        item.remove()
        self.resumable = [s for s in self.resumable if s["id"] != session_id]
        child_count = len(list(lv.children))
        if child_count > 0:
            lv.index = min(idx, child_count - 1) if idx > 0 else 0

    def _submit(self) -> None:
        lv = self.query_one("#session-picker", ListView)
        name_input = self.query_one("#session-name", Input)
        item = lv.highlighted_child
        if isinstance(item, ListItem):
            session_id = item.name
            self.dismiss({
                "name": name_input.value,
                "resume_session_id": None if session_id == "__new__" else session_id,
            })

    def action_cancel(self) -> None:
        self.dismiss(None)


class CloneScreen(_KeyboardModalScreen):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, default_clone_path: str) -> None:
        super().__init__()
        self.default_clone_path = default_clone_path

    def compose(self) -> ComposeResult:
        with Vertical(id="clone-dialog"):
            yield Label("Clone Repository", id="clone-title")
            yield Label("Repository URL:")
            yield Input(placeholder="https://github.com/...", id="clone-url")
            yield Label(f"Clone into: {self.default_clone_path}")
            with Horizontal(id="clone-buttons"):
                yield Button("Clone & Open", variant="primary", id="btn-clone")
                yield Button("Cancel", id="btn-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-clone":
            url = self.query_one("#clone-url", Input).value.strip()
            if url:
                self.dismiss({"url": url, "clone_path": self.default_clone_path})
            else:
                self.dismiss(None)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class FolderExistsScreen(_KeyboardModalScreen):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, path: str) -> None:
        super().__init__()
        self.folder_path = path

    def compose(self) -> ComposeResult:
        with Vertical(id="exists-dialog"):
            yield Label(f"{self.folder_path} already exists.")
            with RadioSet(id="exists-options"):
                yield RadioButton("Open session in existing folder", value=True)
                yield RadioButton("Pull latest and open session")
                yield RadioButton("Cancel")
            with Horizontal(id="exists-buttons"):
                yield Button("OK", variant="primary", id="btn-ok")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        radio = self.query_one("#exists-options", RadioSet)
        choices = ["open", "pull", "cancel"]
        idx = radio.pressed_index
        choice = choices[idx] if idx < len(choices) else "cancel"
        self.dismiss(choice if choice != "cancel" else None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmKillScreen(_KeyboardModalScreen):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, session_name: str) -> None:
        super().__init__()
        self.session_name = session_name

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog"):
            yield Label(f"Kill session '{self.session_name}'?")
            yield Label("The Claude Code process will be terminated.")
            with Horizontal(id="confirm-buttons"):
                yield Button("Kill", variant="error", id="btn-kill")
                yield Button("Cancel", variant="default", id="btn-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "btn-kill")

    def action_cancel(self) -> None:
        self.dismiss(False)


class UrlPickerScreen(_KeyboardModalScreen):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, urls: list[tuple[str, bool]]) -> None:
        super().__init__()
        self.urls = urls

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-dialog"):
            yield Label("Open URL:", id="picker-title")
            items = []
            for url, alive in self.urls:
                icon = "🟢" if alive else "🔴"
                items.append(ListItem(Label(f"  {icon} http://{url}"), name=url))
            yield ListView(*items, id="url-list", initial_index=0)

    def on_key(self, event) -> None:
        lv = self.query_one("#url-list", ListView)
        if event.key == "up":
            lv.action_cursor_up()
            event.prevent_default()
            event.stop()
        elif event.key == "down":
            lv.action_cursor_down()
            event.prevent_default()
            event.stop()
        elif event.key == "enter":
            item = lv.highlighted_child
            if isinstance(item, ListItem) and item.name:
                self.dismiss(item.name)
            event.prevent_default()
            event.stop()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item.name:
            self.dismiss(event.item.name)

    def action_cancel(self) -> None:
        self.dismiss(None)


class RenameScreen(_KeyboardModalScreen):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, current_name: str) -> None:
        super().__init__()
        self.current_name = current_name

    def compose(self) -> ComposeResult:
        with Vertical(id="rename-dialog"):
            yield Label("Rename session:")
            yield Input(value=self.current_name, id="rename-input")
            with Horizontal(id="rename-buttons"):
                yield Button("Rename", variant="primary", id="btn-rename")
                yield Button("Cancel", id="btn-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-rename":
            new_name = self.query_one("#rename-input", Input).value.strip()
            self.dismiss(new_name if new_name else None)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ErrorScreen(_KeyboardModalScreen):
    BINDINGS = [("escape", "cancel", "Cancel"), ("enter", "cancel", "Cancel")]

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

    def action_cancel(self) -> None:
        self.dismiss(None)
