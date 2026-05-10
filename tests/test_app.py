import pytest
from unittest.mock import patch, MagicMock
from hive.widgets.session_list import SessionListItem, SessionData
from hive.detector import SessionState
from hive.config import HiveConfig, HiveState


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


class TestHiveAppSmoke:
    @pytest.mark.asyncio
    @patch("hive.app.TmuxClient")
    @patch("hive.app.HiveConfig.load_default")
    @patch("hive.app.HiveState.load_default")
    async def test_app_mounts(self, mock_state, mock_config, mock_tmux):
        mock_config.return_value = HiveConfig.defaults()
        mock_state.return_value = HiveState()
        mock_tmux_instance = MagicMock()
        mock_tmux_instance.list_windows.return_value = []
        mock_tmux.return_value = mock_tmux_instance

        from hive.app import HiveApp
        app = HiveApp()
        async with app.run_test() as pilot:
            from textual.widgets import Label
            header = app.query_one("#header", Label)
            assert "hive" in str(header.render()).lower()
            footer = app.query_one("#footer-bar", Label)
            assert "n:new" in str(footer.render())
