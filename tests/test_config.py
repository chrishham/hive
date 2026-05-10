import json
import os
from pathlib import Path

import pytest

from hive.config import HiveConfig, HiveState


class TestHiveConfig:
    def test_default_config(self):
        config = HiveConfig.defaults()
        assert config.scan_paths == [str(Path.home() / "Projects")]
        assert config.clone_path == str(Path.home() / "Projects")
        assert config.refresh_interval_ms == 2500
        assert config.preview_lines == 20
        assert config.idle_timeout_seconds == 300
        assert config.tmux_session_name == "hive"

    def test_load_from_toml(self, tmp_path):
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            '[projects]\nscan_paths = ["/tmp/a"]\nclone_path = "/tmp/b"\n\n'
            "[display]\nrefresh_interval_ms = 1000\npreview_lines = 30\nidle_timeout_seconds = 600\n\n"
            '[tmux]\nsession_name = "myhive"\n'
        )
        config = HiveConfig.load(config_file)
        assert config.scan_paths == ["/tmp/a"]
        assert config.clone_path == "/tmp/b"
        assert config.refresh_interval_ms == 1000
        assert config.preview_lines == 30
        assert config.idle_timeout_seconds == 600
        assert config.tmux_session_name == "myhive"

    def test_load_missing_file_returns_defaults(self, tmp_path):
        config = HiveConfig.load(tmp_path / "nonexistent.toml")
        assert config.scan_paths == HiveConfig.defaults().scan_paths


class TestHiveState:
    def test_empty_state(self):
        state = HiveState()
        assert state.sessions == {}
        assert state.projects == {}

    def test_save_and_load(self, tmp_path):
        state_file = tmp_path / "state.json"
        state = HiveState()
        state.sessions["my-session"] = {
            "project_path": "/tmp/proj",
            "created_at": "2026-05-10T10:00:00Z",
            "last_used": "2026-05-10T10:00:00Z",
            "tmux_window": 1,
            "claude_session_id": "abc-123",
        }
        state.projects["/tmp/proj"] = {"last_used": "2026-05-10T10:00:00Z"}
        state.save(state_file)

        loaded = HiveState.load(state_file)
        assert loaded.sessions["my-session"]["project_path"] == "/tmp/proj"
        assert loaded.projects["/tmp/proj"]["last_used"] == "2026-05-10T10:00:00Z"

    def test_load_missing_file_returns_empty(self, tmp_path):
        state = HiveState.load(tmp_path / "nonexistent.json")
        assert state.sessions == {}

    def test_update_project_last_used(self):
        state = HiveState()
        state.touch_project("/tmp/proj")
        assert "/tmp/proj" in state.projects
        assert "last_used" in state.projects["/tmp/proj"]

    def test_add_session(self):
        state = HiveState()
        state.add_session("my-sess", "/tmp/proj", 2, "uuid-here")
        assert state.sessions["my-sess"]["tmux_window"] == 2
        assert state.sessions["my-sess"]["claude_session_id"] == "uuid-here"

    def test_remove_session(self):
        state = HiveState()
        state.add_session("my-sess", "/tmp/proj", 2, "uuid-here")
        state.remove_session("my-sess")
        assert "my-sess" not in state.sessions
