from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, ListView, ListItem, RadioButton, RadioSet


class ProjectPickerScreen(ModalScreen[dict | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, projects: list[dict]) -> None:
        super().__init__()
        self.projects = projects
        self._project_map: dict[str, dict] = {p["path"]: p for p in projects}

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-dialog"):
            yield Label("Select a project:", id="picker-title")
            yield Input(placeholder="Type to filter...", id="picker-filter")
            yield ListView(
                *[
                    ListItem(Label(f"{p['name']}  {p['path']}  {p.get('age', '')}"), name=p["path"])
                    for p in self.projects
                ],
                id="picker-list",
            )

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
        list_view = self.query_one("#picker-list", ListView)
        first_visible = None
        for i, child in enumerate(list_view.children):
            if isinstance(child, ListItem) and child.name:
                proj = self._project_map.get(child.name, {})
                text = proj.get("name", "").lower()
                visible = query in text
                child.display = visible
                if visible and first_visible is None:
                    first_visible = i
        if first_visible is not None:
            list_view.index = first_visible

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        path = event.item.name
        if path:
            self.dismiss({"path": path, "name": Path(path).name})

    def action_cancel(self) -> None:
        self.dismiss(None)


class SessionOptionsScreen(ModalScreen[dict | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, project_name: str, has_previous: bool = False) -> None:
        super().__init__()
        self.project_name = project_name
        self.has_previous = has_previous

    def compose(self) -> ComposeResult:
        with Vertical(id="options-dialog"):
            yield Label(f"New session in {self.project_name}", id="options-title")
            with RadioSet(id="session-type"):
                yield RadioButton("New session", value=True)
                if self.has_previous:
                    yield RadioButton("Continue last session")
            yield Input(placeholder="Session name (blank for auto)", id="session-name")
            with Horizontal(id="options-buttons"):
                yield Button("Create", variant="primary", id="btn-create")
                yield Button("Cancel", id="btn-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-create":
            name_input = self.query_one("#session-name", Input)
            radio = self.query_one("#session-type", RadioSet)
            self.dismiss({
                "name": name_input.value,
                "continue_session": radio.pressed_index == 1 if self.has_previous else False,
            })
        else:
            self.dismiss(None)

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
