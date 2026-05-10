import pytest
from hive.widgets.session_list import SessionListItem, SessionData
from hive.detector import SessionState


class TestSessionData:
    def test_create(self):
        data = SessionData(
            name="fix-auth",
            project_path="/tmp/proj",
            tmux_window=1,
            state=SessionState.WAITING,
            model="opus-4.6",
            context_str="1M",
            context_pct=78,
            urls=[("localhost:3000", True), ("localhost:8080", False)],
        )
        assert data.name == "fix-auth"
        assert data.state == SessionState.WAITING
        assert len(data.urls) == 2

    def test_status_icon(self):
        assert SessionData(name="a", project_path="/", tmux_window=1, state=SessionState.WAITING).status_icon == "●"
        assert SessionData(name="a", project_path="/", tmux_window=1, state=SessionState.WORKING).status_icon == "◐"
        assert SessionData(name="a", project_path="/", tmux_window=1, state=SessionState.IDLE).status_icon == "○"
        assert SessionData(name="a", project_path="/", tmux_window=1, state=SessionState.EXITED).status_icon == "✕"

    def test_status_color(self):
        assert SessionData(name="a", project_path="/", tmux_window=1, state=SessionState.WAITING).status_color == "yellow"
        assert SessionData(name="a", project_path="/", tmux_window=1, state=SessionState.WORKING).status_color == "dodgerblue"
        assert SessionData(name="a", project_path="/", tmux_window=1, state=SessionState.IDLE).status_color == "grey"
        assert SessionData(name="a", project_path="/", tmux_window=1, state=SessionState.EXITED).status_color == "red"
