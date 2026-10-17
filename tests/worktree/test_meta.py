"""Worktree 元数据读写。"""

import json

from codepilot.worktree.meta import (
    delete_meta,
    iter_metas,
    meta_path,
    read_meta,
    write_meta,
)
from codepilot.worktree.model import WorktreeMeta


def _meta(name="a", **kw):
    base = dict(name=name, branch=f"codepilot/{name}", base="abc",
                created_at=100.0, role="r", task_id=1)
    base.update(kw)
    return WorktreeMeta(**base)


def test_write_read_roundtrip(tmp_path):
    m = _meta()
    write_meta(tmp_path, m)
    assert read_meta(tmp_path, "a") == m


def test_meta_path_hash_stable(tmp_path):
    p1 = meta_path(tmp_path, "a/b")
    p2 = meta_path(tmp_path, "a/b")
    assert p1 == p2
    assert p1.parent.name == "_meta"
    assert p1.suffix == ".json"


def test_missing_returns_none(tmp_path):
    assert read_meta(tmp_path, "nope") is None


def test_delete_is_idempotent(tmp_path):
    write_meta(tmp_path, _meta())
    delete_meta(tmp_path, "a")
    delete_meta(tmp_path, "a")
    assert read_meta(tmp_path, "a") is None


def test_iter_metas_skips_corrupt(tmp_path):
    write_meta(tmp_path, _meta("a"))
    write_meta(tmp_path, _meta("b", created_at=200.0))
    bad = meta_path(tmp_path, "c")
    bad.write_text("{not json", encoding="utf-8")
    names = [m.name for m in iter_metas(tmp_path)]
    assert names == ["a", "b"]


def test_iter_metas_missing_dir(tmp_path):
    assert iter_metas(tmp_path) == []


def test_corrupt_required_field_returns_none(tmp_path):
    p = meta_path(tmp_path, "a")
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"name": "a"}), encoding="utf-8")
    assert read_meta(tmp_path, "a") is None
