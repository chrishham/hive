from __future__ import annotations

import re

from textual.widgets import Static

ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\].*?\x07|\x1b\[.*?[a-zA-Z]")
BLOCK_CHARS = re.compile(r"[▐▛▜▌▝▘█▀▄▌▐░▒▓]+")
SEPARATOR = re.compile(r"^[─━═╌╍┄┅]{5,}.*$")


def clean_preview(text: str) -> str:
    text = ANSI_ESCAPE.sub("", text)
    lines = []
    for line in text.split("\n"):
        if SEPARATOR.match(line.strip()):
            continue
        line = BLOCK_CHARS.sub("", line)
        stripped = line.strip()
        if stripped:
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

    def set_content(self, text: str, max_lines: int = 20) -> None:
        cleaned = clean_preview(text)
        lines = cleaned.split("\n")
        display = "\n".join(lines[-max_lines:]) if lines else "(no output)"
        self.update(display)

    def clear_content(self) -> None:
        self.update("(no session selected)")
