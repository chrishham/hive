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
    SessionState.BOOTSTRAPPING: "◌",
}

STATUS_COLORS = {
    SessionState.WAITING: "yellow",
    SessionState.WORKING: "dodgerblue",
    SessionState.BOOTSTRAPPING: "dim",
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
        yield Label("", markup=True, id="sl-header")
        yield Label("", id="sl-project")
        yield Label("", id="sl-model")
        yield Label("", id="sl-urls")

    def on_mount(self) -> None:
        self.refresh_content()

    def refresh_content(self) -> None:
        d = self.data
        header = f"[{d.status_color}]{d.status_icon}[/] {d.name}"
        state_label = d.state.value.upper()
        self.query_one("#sl-header", Label).update(f"{header}  [{d.status_color}]{state_label}[/]")
        self.query_one("#sl-project", Label).update(f"  {d.project_path}")

        model_text = ""
        if d.model:
            model_text = f"  {d.model}"
        ctx = d.context_bar()
        if ctx:
            model_text += f"  ctx:{ctx}"
        self.query_one("#sl-model", Label).update(model_text)

        url_lines = []
        for url, alive in d.urls:
            icon = "🟢" if alive else "🔴"
            url_lines.append(f"  {icon} {url}")
        self.query_one("#sl-urls", Label).update("\n".join(url_lines))


class SessionListView(ListView):
    def get_session_data(self) -> SessionData | None:
        item = self.highlighted_child
        if isinstance(item, SessionListItem):
            return item.data
        return None
