import json
import pytest
from unittest.mock import patch, MagicMock
from hive.widgets.session_list import SessionListItem, SessionData
from hive.detector import SessionState
from hive.config import HiveConfig, HiveState
from hive.safety import InvalidSessionName


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
    @patch("hive.app.hook_installed")
    @patch("hive.app.TmuxClient")
    @patch("hive.app.HiveConfig.load_default")
    @patch("hive.app.HiveState.load_default")
    async def test_app_mounts(self, mock_state, mock_config, mock_tmux, mock_hook_installed):
        mock_config.return_value = HiveConfig.defaults()
        mock_state.return_value = HiveState()
        mock_tmux_instance = MagicMock()
        mock_tmux_instance.list_windows.return_value = []
        mock_tmux.return_value = mock_tmux_instance
        mock_hook_installed.return_value = True

        from hive.app import HiveApp
        app = HiveApp()
        async with app.run_test() as pilot:
            from textual.widgets import Label
            header = app.query_one("#header", Label)
            assert "hive" in str(header.render()).lower()
            footer = app.query_one("#footer-bar", Label)
            assert "n:new" in str(footer.render())
        mock_hook_installed.assert_called_once()

    @pytest.mark.asyncio
    @patch("hive.app.hook_installed")
    @patch("hive.app.TmuxClient")
    @patch("hive.app.HiveConfig.load_default")
    @patch("hive.app.HiveState.load_default")
    async def test_hook_state_overrides_pane_detection(
        self, mock_state, mock_config, mock_tmux, mock_hook_installed, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("HOME", str(tmp_path))
        state_file = tmp_path / ".claude" / "hive" / "state" / "sess1.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps({"state": "working"}))

        mock_config.return_value = HiveConfig.defaults()
        mock_state.return_value = HiveState()
        mock_tmux_instance = MagicMock()
        mock_tmux_instance.list_windows.return_value = [
            {"index": 1, "name": "sess1", "alive": True}
        ]
        # Pane text would otherwise classify as WAITING
        mock_tmux_instance.capture_pane.return_value = (
            "Claude Code v2.1\n╭───╮\n│ > │\n╰───╯\n"
        )
        mock_tmux_instance.capture_pane_scrollback.return_value = ""
        mock_tmux.return_value = mock_tmux_instance
        mock_hook_installed.return_value = True

        from hive.app import HiveApp
        app = HiveApp()
        async with app.run_test() as pilot:
            await app._refresh_sessions()
            data = app.session_data_map["sess1"]
            assert data.state == SessionState.WORKING


@pytest.mark.asyncio
@patch("hive.app.hook_installed")
@patch("hive.app.TmuxClient")
@patch("hive.app.HiveConfig.load_default")
@patch("hive.app.HiveState.load_default")
async def test_create_session_validates_name(mock_state, mock_config, mock_tmux, mock_hook_installed):
    mock_config.return_value = HiveConfig.defaults()
    mock_state.return_value = HiveState()
    mock_tmux.return_value = MagicMock()
    mock_hook_installed.return_value = True
    from hive.app import HiveApp
    app = HiveApp()
    with pytest.raises(InvalidSessionName):
        await app._create_session("/tmp", "bad name; rm -rf /")


@pytest.mark.asyncio
@patch("hive.app.hook_installed")
@patch("hive.app.TmuxClient")
@patch("hive.app.HiveConfig.load_default")
@patch("hive.app.HiveState.load_default")
async def test_restore_sanitizes_bad_existing_state(
    mock_state, mock_config, mock_tmux, mock_hook_installed, tmp_path
):
    mock_config.return_value = HiveConfig.defaults()
    state = HiveState()
    state.sessions = {
        "bad name": {
            "project_path": str(tmp_path),
            "tmux_window": 1,
            "claude_session_id": "",
        }
    }
    mock_state.return_value = state
    mock_hook_installed.return_value = True

    captured: dict = {}

    def fake_new_window(name, cwd, command_args, env=None):
        captured["name"] = name
        captured["env_session"] = env["HIVE_SESSION"] if env else ""
        captured["command_args"] = command_args
        return 99

    tmux_instance = MagicMock()
    tmux_instance.list_windows.return_value = []
    tmux_instance.new_window.side_effect = fake_new_window
    tmux_instance.select_window.return_value = None
    mock_tmux.return_value = tmux_instance

    from hive.app import HiveApp
    app = HiveApp()
    # Stub state.save_default to a no-op to avoid touching the user's real state file
    app.state.save_default = lambda: None
    app._restore_sessions()

    assert captured["name"] == "bad_name"
    assert captured["env_session"] == "bad_name"
    assert "bad name" not in app.state.sessions
    assert "bad_name" in app.state.sessions


@pytest.mark.asyncio
@patch("hive.app.hook_installed")
@patch("hive.app.TmuxClient")
@patch("hive.app.HiveConfig.load_default")
@patch("hive.app.HiveState.load_default")
async def test_restore_does_not_infinite_loop_on_long_colliding_names(
    mock_state, mock_config, mock_tmux, mock_hook_installed, tmp_path
):
    mock_config.return_value = HiveConfig.defaults()
    state = HiveState()
    long_a = "a" * 64
    state.sessions = {
        long_a: {"project_path": str(tmp_path), "tmux_window": 1, "claude_session_id": ""},
        long_a + " bad": {"project_path": str(tmp_path), "tmux_window": 2, "claude_session_id": ""},
    }
    mock_state.return_value = state
    mock_hook_installed.return_value = True

    tmux_instance = MagicMock()
    tmux_instance.list_windows.return_value = []
    tmux_instance.new_window.return_value = 99
    tmux_instance.select_window.return_value = None
    mock_tmux.return_value = tmux_instance

    from hive.app import HiveApp
    app = HiveApp()
    app.state.save_default = lambda: None
    # Should complete in finite time, not hang
    app._restore_sessions()
    # Original 64-a is fine; the colliding "long_a bad" gets sanitized & deduped
    assert long_a in app.state.sessions
    assert long_a + " bad" not in app.state.sessions


@pytest.mark.asyncio
@patch("hive.app.hook_installed")
@patch("hive.app.TmuxClient")
@patch("hive.app.HiveConfig.load_default")
@patch("hive.app.HiveState.load_default")
async def test_refresh_exception_is_caught_and_banner_set(
    mock_state, mock_config, mock_tmux, mock_hook_installed
):
    mock_config.return_value = HiveConfig.defaults()
    mock_state.return_value = HiveState()
    mock_tmux.return_value = MagicMock()
    mock_hook_installed.return_value = True
    from hive.app import HiveApp
    app = HiveApp()

    async def boom():
        raise RuntimeError("boom")

    app._refresh_sessions = boom
    captured: dict[str, str] = {}
    app._set_error_banner = lambda text: captured.setdefault("text", text)
    app._clear_error_banner = lambda: captured.pop("text", None)

    await app._poll_once()
    assert "boom" in captured["text"]


@pytest.mark.asyncio
@patch("hive.app.hook_installed")
@patch("hive.app.TmuxClient")
@patch("hive.app.HiveConfig.load_default")
@patch("hive.app.HiveState.load_default")
async def test_refresh_success_clears_banner(
    mock_state, mock_config, mock_tmux, mock_hook_installed
):
    mock_config.return_value = HiveConfig.defaults()
    mock_state.return_value = HiveState()
    mock_tmux.return_value = MagicMock()
    mock_hook_installed.return_value = True
    from hive.app import HiveApp
    app = HiveApp()

    async def ok():
        return None

    app._refresh_sessions = ok
    cleared = {"flag": False}
    app._set_error_banner = lambda _: None
    app._clear_error_banner = lambda: cleared.update(flag=True)

    await app._poll_once()
    assert cleared["flag"] is True


@patch("hive.app.hook_installed")
@patch("hive.app.TmuxClient")
@patch("hive.app.HiveConfig.load_default")
@patch("hive.app.HiveState.load_default")
def test_status_bar_escapes_hash_in_session_name(
    mock_state, mock_config, mock_tmux, mock_hook_installed
):
    mock_config.return_value = HiveConfig.defaults()
    mock_state.return_value = HiveState()
    tmux_instance = MagicMock()
    captured: dict[str, str] = {}
    tmux_instance.set_status_bar = lambda t: captured.setdefault("text", t)
    mock_tmux.return_value = tmux_instance
    mock_hook_installed.return_value = True

    from hive.app import HiveApp
    app = HiveApp()
    app.session_data_map = {
        "a#b": SessionData(
            name="a#b", project_path="", tmux_window=1, state=SessionState.WAITING
        ),
    }
    app._update_tmux_status()
    assert "a##b ●" in captured["text"]
    assert "a#b ●" not in captured["text"]


def test_validate_clone_target_rejects_dotdot(tmp_path):
    from hive.app import _validate_clone_target
    with pytest.raises(ValueError):
        _validate_clone_target(str(tmp_path), "..")


def test_validate_clone_target_rejects_separators(tmp_path):
    from hive.app import _validate_clone_target
    with pytest.raises(ValueError):
        _validate_clone_target(str(tmp_path), "a/b")


def test_validate_clone_target_accepts_normal_repo(tmp_path):
    from hive.app import _validate_clone_target
    assert _validate_clone_target(str(tmp_path), "myrepo") == str(tmp_path / "myrepo")


class TestSessionSortKey:
    def _make(self, name, state, waiting_since=None):
        return SessionData(
            name=name, project_path="/", tmux_window=1,
            state=state, waiting_since=waiting_since,
        )

    def test_waiting_before_working(self):
        from hive.app import _session_sort_key
        items = [
            self._make("z-working", SessionState.WORKING),
            self._make("a-waiting", SessionState.WAITING, "2026-05-11T10:00:00+00:00"),
        ]
        sorted_items = sorted(items, key=_session_sort_key)
        assert [s.name for s in sorted_items] == ["a-waiting", "z-working"]

    def test_waiting_oldest_first(self):
        from hive.app import _session_sort_key
        items = [
            self._make("recent", SessionState.WAITING, "2026-05-11T12:00:00+00:00"),
            self._make("oldest", SessionState.WAITING, "2026-05-11T08:00:00+00:00"),
            self._make("middle", SessionState.WAITING, "2026-05-11T10:00:00+00:00"),
        ]
        sorted_items = sorted(items, key=_session_sort_key)
        assert [s.name for s in sorted_items] == ["oldest", "middle", "recent"]

    def test_waiting_without_timestamp_sorts_last_among_waiting(self):
        from hive.app import _session_sort_key
        items = [
            self._make("no-ts", SessionState.WAITING, None),
            self._make("with-ts", SessionState.WAITING, "2026-05-11T10:00:00+00:00"),
        ]
        sorted_items = sorted(items, key=_session_sort_key)
        assert [s.name for s in sorted_items] == ["with-ts", "no-ts"]

    def test_working_sessions_sort_by_name(self):
        from hive.app import _session_sort_key
        items = [
            self._make("zebra", SessionState.WORKING),
            self._make("alpha", SessionState.WORKING),
        ]
        sorted_items = sorted(items, key=_session_sort_key)
        assert [s.name for s in sorted_items] == ["alpha", "zebra"]


class TestValidateCloneUrl:
    @pytest.mark.parametrize("url", [
        "https://github.com/user/repo.git",
        "http://example.com/repo",
        "ssh://git@github.com/user/repo.git",
        "git://github.com/user/repo.git",
        "git@github.com:user/repo.git",
        "git@gitlab.example.com:group/sub/repo.git",
    ])
    def test_accepts_valid(self, url):
        from hive.app import _validate_clone_url
        assert _validate_clone_url(url) == url

    @pytest.mark.parametrize("url", [
        "",
        "   ",
        "-upload-pack=evil",
        "--upload-pack=evil",
        "-",
        "/local/path",
        "file:///etc/passwd",
        "ext::sh -c id",
        "javascript:alert(1)",
        "ftp://example.com/repo",
    ])
    def test_rejects_invalid(self, url):
        from hive.app import _validate_clone_url
        with pytest.raises(ValueError):
            _validate_clone_url(url)

    def test_rejects_url_with_newline(self):
        from hive.app import _validate_clone_url
        with pytest.raises(ValueError):
            _validate_clone_url("https://example.com/repo\nrm -rf /")


class TestRedactGitOutput:
    def test_redacts_basic_auth_in_url(self):
        from hive.app import _redact_git_output
        out = _redact_git_output("fatal: failed to clone https://abc123token@github.com/u/r.git")
        assert "abc123token" not in out
        assert "REDACTED" in out

    def test_redacts_user_password_in_url(self):
        from hive.app import _redact_git_output
        out = _redact_git_output("ssh://user:secretpw@host/repo.git")
        assert "secretpw" not in out
        assert "user" not in out or "REDACTED" in out

    def test_passthrough_when_no_credentials(self):
        from hive.app import _redact_git_output
        msg = "fatal: repository 'https://github.com/u/r.git/' not found"
        assert _redact_git_output(msg) == msg

    def test_handles_empty(self):
        from hive.app import _redact_git_output
        assert _redact_git_output("") == ""


@pytest.mark.asyncio
@patch("hive.app.hook_installed")
@patch("hive.app.TmuxClient")
@patch("hive.app.HiveConfig.load_default")
@patch("hive.app.HiveState.load_default")
async def test_rename_active_session_rejected(
    mock_state, mock_config, mock_tmux, mock_hook_installed
):
    mock_config.return_value = HiveConfig.defaults()
    mock_state.return_value = HiveState()
    mock_tmux_instance = MagicMock()
    mock_tmux_instance.list_windows.return_value = []
    mock_tmux.return_value = mock_tmux_instance
    mock_hook_installed.return_value = True

    from hive.app import HiveApp
    app = HiveApp()

    # Simulate an active session
    app.session_data_map = {
        "active-sess": SessionData(
            name="active-sess",
            project_path="/tmp/proj",
            tmux_window=1,
            state=SessionState.WAITING,
        ),
    }

    # Stub _set_warning_banner to capture calls
    warning_banner_text = {"text": ""}
    app._set_warning_banner = lambda text: warning_banner_text.update(text=text)

    # Track if ErrorScreen was pushed
    error_screen_args = {}

    async def mock_push_screen_wait(screen):
        if screen.__class__.__name__ == "ErrorScreen":
            error_screen_args["title"] = screen.error_title
            error_screen_args["message"] = screen.error_message
            return None
        elif screen.__class__.__name__ == "RenameScreen":
            # This should NOT be called
            return "new-name"
        return None

    app.push_screen_wait = mock_push_screen_wait

    # Mock the list view to return our active session
    from hive.widgets.session_list import SessionListView
    list_view = MagicMock(spec=SessionListView)
    list_view.get_session_data.return_value = app.session_data_map["active-sess"]

    app.query_one = lambda selector, expected_type=None: list_view

    # Call the underlying wrapped function directly to avoid Worker complexity
    await app.action_rename_session.__wrapped__(app)

    # Verify ErrorScreen was shown
    assert "title" in error_screen_args
    assert "Cannot rename" in error_screen_args["title"]
    assert "Kill the session first" in error_screen_args["message"]

    # Verify tmux.rename_window was NOT called
    mock_tmux_instance.rename_window.assert_not_called()
