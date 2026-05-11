import json
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

    def test_status_color(self):
        assert SessionData(name="a", project_path="/", tmux_window=1, state=SessionState.WAITING).status_color == "yellow"
        assert SessionData(name="a", project_path="/", tmux_window=1, state=SessionState.WORKING).status_color == "dodgerblue"


class TestHiveAppSmoke:
    @pytest.mark.asyncio
    @patch("hive.install_hooks.install_hooks")
    @patch("hive.app.TmuxClient")
    @patch("hive.app.HiveConfig.load_default")
    @patch("hive.app.HiveState.load_default")
    async def test_app_mounts(self, mock_state, mock_config, mock_tmux, mock_install_hooks):
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
        mock_install_hooks.assert_called_once()

    @pytest.mark.asyncio
    @patch("hive.install_hooks.install_hooks")
    @patch("hive.app.TmuxClient")
    @patch("hive.app.HiveConfig.load_default")
    @patch("hive.app.HiveState.load_default")
    async def test_hook_state_overrides_pane_detection(
        self, mock_state, mock_config, mock_tmux, mock_install_hooks, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("HOME", str(tmp_path))
        state_file = tmp_path / ".claude" / "hive" / "state" / "sess1.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps({"state": "working"}))

        mock_config.return_value = HiveConfig.defaults()
        mock_state.return_value = HiveState()
        mock_tmux_instance = MagicMock()
        mock_tmux_instance.list_windows.return_value = [
            {"index": 1, "name": "sess1", "active": True}
        ]
        # Pane text would otherwise classify as WAITING
        mock_tmux_instance.capture_pane.return_value = (
            "Claude Code v2.1\n╭───╮\n│ > │\n╰───╯\n"
        )
        mock_tmux_instance.capture_pane_scrollback.return_value = ""
        mock_tmux.return_value = mock_tmux_instance

        from hive.app import HiveApp
        app = HiveApp()
        async with app.run_test() as pilot:
            await app._refresh_sessions()
            data = app.session_data_map["sess1"]
            assert data.state == SessionState.WORKING
