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

    def set_content(self, text: str, max_lines: int = 20) -> None:
        lines = text.strip().split("\n")
        display = "\n".join(lines[-max_lines:]) if lines else "(no output)"
        self.update(display)

    def clear_content(self) -> None:
        self.update("(no session selected)")
