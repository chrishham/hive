from datetime import datetime
from pathlib import Path

from hive.persistence import StatePersistence
from hive.session import Session


def make_session(name="proj-001", id_="abc", path="/tmp/p", csid="uuid"):
    return Session(
        id=id_,
        name=name,
        project_path=Path(path),
        created_at=datetime(2026, 5, 10),
        claude_session_id=csid,
    )


def test_save_and_load_roundtrip(tmp_path):
    sp = StatePersistence(tmp_path / "state.json")
    sp.save([make_session(), make_session(name="other-001", id_="def")])
    loaded = sp.load()
    assert len(loaded) == 2
    assert {r["name"] for r in loaded} == {"proj-001", "other-001"}


def test_load_returns_empty_when_file_missing(tmp_path):
    sp = StatePersistence(tmp_path / "nonexistent.json")
    assert sp.load() == []


def test_load_returns_empty_when_file_corrupt(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("{not json")
    sp = StatePersistence(p)
    assert sp.load() == []


def test_save_creates_parent_dir(tmp_path):
    sp = StatePersistence(tmp_path / "subdir" / "state.json")
    sp.save([make_session()])
    assert (tmp_path / "subdir" / "state.json").exists()


def test_save_uses_atomic_write(tmp_path):
    """Save should not leave a partial file even if it fails mid-write."""
    sp = StatePersistence(tmp_path / "state.json")
    sp.save([make_session()])
    # tmp file should not be left behind
    assert not list(tmp_path.glob("state.json.tmp.*"))
