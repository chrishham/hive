from __future__ import annotations

from textual.widgets import Static


class PreviewPane(Static):
    DEFAULT_CSS = """
    PreviewPane {
        width: 1fr;
        height: 1fr;
        padding: 0 1;
        overflow-y: auto;
    }
    """

    def __init__(self, *args, **kwargs) -> None:
        kwargs.setdefault("markup", False)
        super().__init__(*args, **kwargs)

    def set_content(self, synopsis: str) -> None:
        self.update(synopsis if synopsis else "(no session selected)")

    def clear_content(self) -> None:
        self.update("(no session selected)")
