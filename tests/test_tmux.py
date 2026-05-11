from unittest.mock import patch, MagicMock
import subprocess
import pytest

from hive.tmux import TmuxClient
from hive.safety import TmuxError


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
            text=False,
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
            text=False,
        )
        mock_run.assert_any_call(
            ["tmux", "set-option", "-t", "test-hive", "mouse", "on"],
            capture_output=True,
            text=False,
        )

    @patch("hive.tmux.subprocess.run")
    def test_list_windows_tab_delimited(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="0\tdashboard\t0\t0\t0\n1\tmy:session\t1234\t1\t0\n2\tother\t5678\t0\t1\n",
        )
        windows = self.tmux.list_windows()
        assert windows == [
            {"index": 0, "name": "dashboard", "alive": False, "active": False, "last_active": False},
            {"index": 1, "name": "my:session", "alive": True, "active": True, "last_active": False},
            {"index": 2, "name": "other", "alive": True, "active": False, "last_active": True},
        ]
        # Assert format string uses tab separator and includes active flags
        called_args = mock_run.call_args.args[0]
        assert (
            "#{window_index}\t#{window_name}\t#{pane_pid}\t#{window_active}\t#{window_last_flag}"
            in called_args
        )

    @patch("hive.tmux.subprocess.run")
    def test_list_windows_backward_compat_three_fields(self, mock_run):
        # Older format (3 fields) should still parse, defaulting flags to False.
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="0\tdashboard\t0\n1\twin\t1234\n",
        )
        windows = self.tmux.list_windows()
        assert windows == [
            {"index": 0, "name": "dashboard", "alive": False, "active": False, "last_active": False},
            {"index": 1, "name": "win", "alive": True, "active": False, "last_active": False},
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
            ["tmux", "capture-pane", "-t", "test-hive:1", "-p", "-S", "-1000"],
            capture_output=True,
            text=True,
        )

    @patch("hive.tmux.subprocess.run")
    def test_new_window_with_command_args_list(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="3\n")
        idx = self.tmux.new_window(
            "my-window", "/tmp/proj",
            ["claude", "--name", "my window; rm -rf /"],
        )
        assert idx == 3
        called = mock_run.call_args.args[0]
        # The command appears as a single shlex-joined string at the end.
        assert called[-1] == "claude --name 'my window; rm -rf /'"

    @patch("hive.tmux.subprocess.run")
    def test_new_window_raises_tmux_error_on_nonzero(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="boom")
        with pytest.raises(TmuxError, match="boom"):
            self.tmux.new_window("w", "/tmp", ["claude"])

    @patch("hive.tmux.subprocess.run")
    def test_new_window_raises_tmux_error_on_non_numeric(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="not-a-number\n", stderr="")
        with pytest.raises(TmuxError, match="non-numeric"):
            self.tmux.new_window("w", "/tmp", ["claude"])

    @patch("hive.tmux.subprocess.run")
    def test_kill_window(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        self.tmux.kill_window(2)
        mock_run.assert_called_once_with(
            ["tmux", "kill-window", "-t", "test-hive:2"],
            capture_output=True,
            text=False,
        )

    @patch("hive.tmux.subprocess.run")
    def test_select_window(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        self.tmux.select_window(3)
        mock_run.assert_called_once_with(
            ["tmux", "select-window", "-t", "test-hive:3"],
            capture_output=True,
            text=False,
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
            text=False,
        )

    @patch("hive.tmux.subprocess.run")
    def test_disable_status(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        self.tmux.disable_status()
        mock_run.assert_called_once_with(
            ["tmux", "set-option", "-t", "test-hive", "status", "off"],
            capture_output=True,
            text=False,
        )


def test_new_window_with_env(monkeypatch):
    from hive.tmux import TmuxClient
    captured = {}

    def fake_run(self, args, text=False):
        captured["args"] = args
        class R: returncode = 0; stdout = "5"; stderr = ""
        return R()

    monkeypatch.setattr(TmuxClient, "_run", fake_run)
    client = TmuxClient("hive")
    idx = client.new_window("foo", "/tmp", ["claude"], env={"HIVE_SESSION": "foo"})
    assert idx == 5
    assert "-e" in captured["args"]
    e_idx = captured["args"].index("-e")
    assert captured["args"][e_idx + 1] == "HIVE_SESSION=foo"
