from unittest.mock import patch, MagicMock
import subprocess

from hive.tmux import TmuxClient


class TestTmuxClient:
    def setup_method(self):
        self.tmux = TmuxClient(session_name="test-hive")

    @patch("hive.tmux.subprocess.run")
    def test_session_exists_true(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        assert self.tmux.session_exists() is True
        mock_run.assert_called_once_with(
            ["tmux", "has-session", "-t", "test-hive"],
            capture_output=True,
        )

    @patch("hive.tmux.subprocess.run")
    def test_session_exists_false(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        assert self.tmux.session_exists() is False

    @patch("hive.tmux.subprocess.run")
    def test_create_session(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        self.tmux.create_session()
        assert mock_run.call_count == 2
        mock_run.assert_any_call(
            ["tmux", "new-session", "-d", "-s", "test-hive", "-x", "200", "-y", "50"],
            capture_output=True,
        )
        mock_run.assert_any_call(
            ["tmux", "set-option", "-t", "test-hive", "mouse", "on"],
            capture_output=True,
        )

    @patch("hive.tmux.subprocess.run")
    def test_list_windows(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="0:dashboard:0\n1:my-session:1\n2:other:1\n",
        )
        windows = self.tmux.list_windows()
        assert windows == [
            {"index": 0, "name": "dashboard", "active": False},
            {"index": 1, "name": "my-session", "active": True},
            {"index": 2, "name": "other", "active": True},
        ]

    @patch("hive.tmux.subprocess.run")
    def test_capture_pane(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="line1\nline2\nline3\n",
        )
        output = self.tmux.capture_pane(1)
        assert output == "line1\nline2\nline3\n"
        mock_run.assert_called_once_with(
            ["tmux", "capture-pane", "-t", "test-hive:1", "-p", "-S", "-100"],
            capture_output=True,
            text=True,
        )

    @patch("hive.tmux.subprocess.run")
    def test_capture_pane_scrollback(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="scrollback\n")
        output = self.tmux.capture_pane_scrollback(1)
        assert output == "scrollback\n"
        mock_run.assert_called_once_with(
            ["tmux", "capture-pane", "-t", "test-hive:1", "-p", "-S", "-"],
            capture_output=True,
            text=True,
        )

    @patch("hive.tmux.subprocess.run")
    def test_new_window(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="3\n")
        idx = self.tmux.new_window("my-window", "/tmp/proj", "claude --name my-window")
        assert idx == 3

    @patch("hive.tmux.subprocess.run")
    def test_kill_window(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        self.tmux.kill_window(2)
        mock_run.assert_called_once_with(
            ["tmux", "kill-window", "-t", "test-hive:2"],
            capture_output=True,
        )

    @patch("hive.tmux.subprocess.run")
    def test_select_window(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        self.tmux.select_window(3)
        mock_run.assert_called_once_with(
            ["tmux", "select-window", "-t", "test-hive:3"],
            capture_output=True,
        )

    @patch("hive.tmux.subprocess.run")
    def test_is_pane_alive_true(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="1\n")
        assert self.tmux.is_pane_alive(1) is True

    @patch("hive.tmux.subprocess.run")
    def test_is_pane_alive_false(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="0\n")
        assert self.tmux.is_pane_alive(1) is False

    @patch("hive.tmux.subprocess.run")
    def test_set_window_option(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        self.tmux.set_window_option(0, "remain-on-exit", "on")
        mock_run.assert_called_once_with(
            ["tmux", "set-window-option", "-t", "test-hive:0", "remain-on-exit", "on"],
            capture_output=True,
        )

    @patch("hive.tmux.subprocess.run")
    def test_set_status_bar(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        self.tmux.set_status_bar("hive: 2 sessions | ● 1 waiting")
        assert mock_run.call_count == 2
        mock_run.assert_any_call(
            ["tmux", "set-option", "-t", "test-hive", "status-left-length", "100"],
            capture_output=True,
        )
        mock_run.assert_any_call(
            ["tmux", "set-option", "-t", "test-hive", "status-left", "hive: 2 sessions | ● 1 waiting"],
            capture_output=True,
        )


def test_new_window_with_env(monkeypatch):
    from hive.tmux import TmuxClient
    captured = {}

    def fake_run(self, args, text=False):
        captured["args"] = args
        class R: returncode = 0; stdout = "5"
        return R()

    monkeypatch.setattr(TmuxClient, "_run", fake_run)
    client = TmuxClient("hive")
    idx = client.new_window("foo", "/tmp", "claude", env={"HIVE_SESSION": "foo"})
    assert idx == 5
    assert "-e" in captured["args"]
    e_idx = captured["args"].index("-e")
    assert captured["args"][e_idx + 1] == "HIVE_SESSION=foo"
