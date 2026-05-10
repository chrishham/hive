from __future__ import annotations

from dataclasses import dataclass, field

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Label, ListItem, ListView

from hive.detector import SessionState

STATUS_ICONS = {
    SessionState.WAITING: "●",
    SessionState.WORKING: "◐",
    SessionState.IDLE: "○",
    SessionState.EXITED: "✕",
}

STATUS_COLORS = {
    SessionState.WAITING: "yellow",
    SessionState.WORKING: "dodgerblue",
    SessionState.IDLE: "grey",
    SessionState.EXITED: "red",
}


@dataclass
class SessionData:
    name: str
    project_path: str
    tmux_window: int
    state: SessionState = SessionState.WORKING
    model: str | None = None
    context_str: str | None = None
    context_pct: int | None = None
    urls: list[tuple[str, bool]] = field(default_factory=list)
    preview_text: str = ""

    @property
    def status_icon(self) -> str:
        return STATUS_ICONS[self.state]

    @property
    def status_color(self) -> str:
        return STATUS_COLORS[self.state]

    def context_bar(self, width: int = 10) -> str:
        if self.context_pct is None:
            return ""
        filled = round(self.context_pct / 100 * width)
        return "█" * filled + "░" * (width - filled) + f" {self.context_pct}%"


class SessionListItem(ListItem):
    def __init__(self, data: SessionData) -> None:
        super().__init__()
        self.data = data

    def compose(self) -> ComposeResult:
        d = self.data
        header = f"[{d.status_color}]{d.status_icon}[/] {d.name}"
        state_label = d.state.value.upper()
        yield Label(f"{header}  [{d.status_color}]{state_label}[/]", markup=True, classes="session-header")
        yield Label(f"  {d.project_path}", classes="session-project")
        if d.model:
            bar = d.context_bar()
            model_line = f"  {d.model}"
            if bar:
                model_line += f"  {bar}"
            yield Label(model_line, classes="session-model")
        for url, alive in d.urls:
            icon = "🟢" if alive else "🔴"
            yield Label(f"  {icon} {url}", classes="session-url")


class SessionListView(ListView):
    def get_session_data(self) -> SessionData | None:
        item = self.highlighted_child
        if isinstance(item, SessionListItem):
            return item.data
        return None
