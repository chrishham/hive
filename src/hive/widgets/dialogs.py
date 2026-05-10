from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, ListView, ListItem, RadioButton, RadioSet

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


class ProjectPickerScreen(ModalScreen[dict | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, projects: list[dict]) -> None:
        super().__init__()
        self.projects = projects
        self._filtered: list[dict] = list(projects)

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-dialog"):
            yield Label("Select a project:", id="picker-title")
            yield Input(placeholder="Type to filter...", id="picker-filter")
            yield ListView(id="picker-list")

    def on_mount(self) -> None:
        self._populate_list()

    def _populate_list(self) -> None:
        list_view = self.query_one("#picker-list", ListView)
        list_view.clear()
        for p in self._filtered:
            list_view.append(ListItem(Label(f"{p['name']}  {p['path']}  {p.get('age', '')}"), name=p["path"]))
        if self._filtered:
            list_view.index = 0

    def on_key(self, event) -> None:
        list_view = self.query_one("#picker-list", ListView)
        if event.key == "up":
            list_view.action_cursor_up()
            event.prevent_default()
            event.stop()
        elif event.key == "down":
            list_view.action_cursor_down()
            event.prevent_default()
            event.stop()
        elif event.key == "enter":
            item = list_view.highlighted_child
            if isinstance(item, ListItem) and item.name:
                self.dismiss({"path": item.name, "name": Path(item.name).name})
            event.prevent_default()
            event.stop()

    def on_input_changed(self, event: Input.Changed) -> None:
        query = event.value.lower()
        self._filtered = [p for p in self.projects if query in p["name"].lower()]
        self._populate_list()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        path = event.item.name
        if path:
            self.dismiss({"path": path, "name": Path(path).name})

    def action_cancel(self) -> None:
        self.dismiss(None)


class SessionOptionsScreen(ModalScreen[dict | None]):
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
        elif event.key == "enter":
            self._submit()
            event.prevent_default()
            event.stop()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-create":
            self._submit()
        else:
            self.dismiss(None)

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


class CloneScreen(ModalScreen[dict | None]):
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


class FolderExistsScreen(ModalScreen[str | None]):
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


class ConfirmKillScreen(ModalScreen[bool]):
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


class UrlPickerScreen(ModalScreen[str | None]):
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


class RenameScreen(ModalScreen[str | None]):
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
