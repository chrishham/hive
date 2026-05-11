# tests/test_safety.py
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from hive.safety import (
    InvalidSessionName,
    TmuxError,
    atomic_write_text,
    escape_tmux_format,
    sanitize_session_name,
    validate_session_name,
)


class TestValidateSessionName:
    def test_accepts_alphanumerics(self):
        validate_session_name("abc")
        validate_session_name("ABC")
        validate_session_name("123")

    def test_accepts_dot_dash_underscore(self):
        validate_session_name("a.b")
        validate_session_name("a-b")
        validate_session_name("a_b")

    def test_accepts_max_length_64(self):
        validate_session_name("a" * 64)

    def test_accepts_realistic_names(self):
        validate_session_name("owui-nbg-001")
        validate_session_name("DevSecOps-001")
        validate_session_name("claude-code-sandbox-001")

    @pytest.mark.parametrize("bad", [
        "", " ", "a b", "a;b", "a$b", "a#b", "a(b", "a/b", "a\\b",
        "a:b", "a\tb", "a\nb", ".", "..", "a" * 65, "kafé", "\x00",
    ])
    def test_rejects(self, bad):
        with pytest.raises(InvalidSessionName):
            validate_session_name(bad)


class TestSanitizeSessionName:
    def test_replaces_forbidden_with_underscore(self):
        assert sanitize_session_name("a b;c") == "a_b_c"

    def test_truncates_to_64(self):
        assert sanitize_session_name("a" * 100) == "a" * 64

    def test_empty_returns_default(self):
        assert sanitize_session_name("") == "session"

    def test_all_bad_returns_default(self):
        assert sanitize_session_name(";;;") == "session"

    def test_keeps_valid_unchanged(self):
        assert sanitize_session_name("owui-nbg-001") == "owui-nbg-001"

    def test_output_is_always_valid(self):
        for raw in ["", "a b", ";;;", "a" * 100, "a/b\\c"]:
            validate_session_name(sanitize_session_name(raw))


class TestAtomicWriteText:
    def test_writes_content(self, tmp_path):
        target = tmp_path / "out.json"
        atomic_write_text(target, '{"k": 1}')
        assert target.read_text() == '{"k": 1}'

    def test_no_temp_left_behind(self, tmp_path):
        target = tmp_path / "out.json"
        atomic_write_text(target, "data")
        leftovers = [p for p in tmp_path.iterdir() if p != target]
        assert leftovers == []

    def test_creates_parent_directory(self, tmp_path):
        target = tmp_path / "nested" / "dir" / "out.json"
        atomic_write_text(target, "data")
        assert target.read_text() == "data"

    def test_concurrent_writers_no_collision(self, tmp_path):
        target = tmp_path / "out.json"
        errors: list[Exception] = []

        def writer(payload: str):
            try:
                atomic_write_text(target, payload)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(f"payload-{i}",)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        final = target.read_text()
        assert final.startswith("payload-")


class TestAtomicWriteTextSymlinkSafety:
    def test_refuses_symlink_target(self, tmp_path):
        target = tmp_path / "out.json"
        elsewhere = tmp_path / "elsewhere.json"
        elsewhere.write_text("victim")
        target.symlink_to(elsewhere)
        with pytest.raises(OSError):
            atomic_write_text(target, "attacker")
        assert elsewhere.read_text() == "victim"

    def test_refuses_symlinked_parent(self, tmp_path):
        real = tmp_path / "real_dir"
        real.mkdir()
        link = tmp_path / "linked_dir"
        link.symlink_to(real)
        with pytest.raises(OSError):
            atomic_write_text(link / "out.json", "data")

    def test_does_not_traverse_symlink_in_parent_chain(self, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        link = tmp_path / "via_link"
        link.symlink_to(outside)
        with pytest.raises(OSError):
            atomic_write_text(link / "sub" / "out.json", "data")
        assert not (outside / "sub").exists()

    def test_no_temp_left_when_target_is_symlink(self, tmp_path):
        target = tmp_path / "out.json"
        elsewhere = tmp_path / "elsewhere.json"
        elsewhere.write_text("victim")
        target.symlink_to(elsewhere)
        with pytest.raises(OSError):
            atomic_write_text(target, "attacker")
        leftovers = [
            p.name for p in tmp_path.iterdir()
            if p.name not in {"out.json", "elsewhere.json"}
        ]
        assert leftovers == []


class TestEscapeTmuxFormat:
    def test_doubles_hash(self):
        assert escape_tmux_format("a#b") == "a##b"

    def test_no_hash_unchanged(self):
        assert escape_tmux_format("hello world") == "hello world"

    def test_idempotent_on_clean_input(self):
        assert escape_tmux_format(escape_tmux_format("clean")) == "clean"

    def test_blocks_format_substitution(self):
        # Without escaping, '#(date)' would be interpreted as a tmux command.
        assert escape_tmux_format("#(date)") == "##(date)"


def test_tmux_error_is_runtime_error():
    assert issubclass(TmuxError, RuntimeError)


def test_invalid_session_name_is_value_error():
    assert issubclass(InvalidSessionName, ValueError)
