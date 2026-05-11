from __future__ import annotations

import re

from textual.widgets import Static

ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\].*?\x07|\x1b\[.*?[a-zA-Z]")
BOX_AND_BLOCK = re.compile(r"[▐▛▜▌▝▘█▀▄░▒▓│║╭╮╰╯┌┐└┘├┤┬┴┼╔╗╚╝╠╣╦╩╬]+")
SEPARATOR = re.compile(r"^─{3,}")


def clean_preview(text: str) -> str:
    text = ANSI_ESCAPE.sub("", text)
    lines = []
    for line in text.split("\n"):
        line = BOX_AND_BLOCK.sub("", line)
        stripped = line.strip()
        if not stripped:
            continue
        if SEPARATOR.match(stripped):
            continue
        lines.append(stripped)
    return "\n".join(lines)


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

    def set_content(self, text: str, max_lines: int = 20) -> None:
        cleaned = clean_preview(text)
        lines = cleaned.split("\n")
        display = "\n".join(lines[-max_lines:]) if lines else "(no output)"
        self.update(display)

    def clear_content(self) -> None:
        self.update("(no session selected)")
