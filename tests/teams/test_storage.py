"""持久化布局与原子 IO。"""

import json
from pathlib import Path

import pytest

from codepilot.teams.model import TeamError, TeamNameError
from codepilot.teams.storage import TeamStorage


def test_team_paths(storage: TeamStorage):
    d = storage.team_dir("alpha")
    assert d.name == "alpha"
    assert storage.member_meta_path("alpha", "bob").name == "bob.json"
    assert storage.mailbox_path("alpha", "lead").name == "lead.jsonl"
    assert storage.mailbox_lock_path("alpha", "bob").name == "bob.lock"


def test_invalid_team_name_cannot_escape(storage: TeamStorage):
    with pytest.raises(TeamNameError):
        storage.team_dir("../evil")
    with pytest.raises(TeamNameError):
        storage.member_meta_path("alpha", "../x")


def test_atomic_json_roundtrip(storage: TeamStorage, tmp_path: Path):
    path = tmp_path / "x.json"
    storage.atomic_write_json(path, {"a": "中文", "n": 1})
    assert storage.read_json(path) == {"a": "中文", "n": 1}


def test_read_missing_default(storage: TeamStorage, tmp_path: Path):
    assert storage.read_json(tmp_path / "missing.json", {"d": 1}) == {"d": 1}


def test_read_invalid_json_raises(storage: TeamStorage, tmp_path: Path):
    path = tmp_path / "bad.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(TeamError):
        storage.read_json(path)


def test_read_json_safe_backs_up(storage: TeamStorage, tmp_path: Path):
    path = tmp_path / "bad.json"
    path.write_text("{", encoding="utf-8")
    assert storage.read_json_safe(path, {"ok": 1}) == {"ok": 1}
    assert not path.exists()
    backups = list(tmp_path.glob("bad.json.corrupt-*"))
    assert backups


def test_jsonl_skips_bad_lines(storage: TeamStorage, tmp_path: Path):
    path = tmp_path / "m.jsonl"
    path.write_text(
        '{"a": 1}\nnot-json\n[]\n{"b": 2}\n', encoding="utf-8")
    rows, bad = storage.read_jsonl(path)
    assert rows == [{"a": 1}, {"b": 2}]
    assert bad == 2


def test_rewrite_jsonl_is_atomic(storage: TeamStorage, tmp_path: Path):
    path = tmp_path / "m.jsonl"
    storage.rewrite_jsonl(path, [{"a": 1}, {"b": 2}])
    assert [json.loads(x) for x in path.read_text().splitlines()] == [
        {"a": 1}, {"b": 2}]


def test_current_pointer_roundtrip(storage: TeamStorage):
    storage.write_current("alpha")
    assert storage.read_current() == "alpha"


def test_current_pointer_invalid(storage: TeamStorage):
    storage.current_path.parent.mkdir(parents=True, exist_ok=True)
    storage.current_path.write_text('{"name": "../evil"}', encoding="utf-8")
    assert storage.read_current() is None


def test_ensure_layout(storage: TeamStorage):
    storage.ensure_team_layout("alpha")
    assert storage.members_dir("alpha").is_dir()
    assert storage.mailboxes_dir("alpha").is_dir()
    assert storage.team_lock_path("alpha").parent.is_dir()
