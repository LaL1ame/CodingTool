"""Worktree 元数据读写：``<root>/_meta/<sha1(name)>.json``。

元数据与工作目录分离，避免写入工作树变成 untracked 改动；
``_meta`` 以下划线开头，无法通过名称校验，不会与合法 worktree 名冲突。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import asdict
from pathlib import Path

from codepilot.worktree.model import WorktreeMeta

logger = logging.getLogger("codepilot.worktree")

META_DIR_NAME = "_meta"
_REQUIRED = ("name", "branch", "base", "created_at")


def meta_path(root: str | Path, name: str) -> Path:
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()
    return Path(root) / META_DIR_NAME / f"{digest}.json"


def write_meta(root: str | Path, meta: WorktreeMeta) -> None:
    """原子写入（临时文件 + os.replace）。"""
    path = meta_path(root, meta.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(asdict(meta), ensure_ascii=False, indent=2),
        encoding="utf-8")
    os.replace(tmp, path)


def _parse(path: Path) -> WorktreeMeta | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("worktree 元数据读取失败 %s: %s", path, e)
        return None
    if not isinstance(data, dict):
        logger.warning("worktree 元数据不是映射: %s", path)
        return None
    if any(key not in data for key in _REQUIRED):
        logger.warning("worktree 元数据缺字段: %s", path)
        return None
    try:
        return WorktreeMeta(
            name=str(data["name"]),
            branch=str(data["branch"]),
            base=str(data["base"]),
            created_at=float(data["created_at"]),
            role=data.get("role"),
            task_id=int(data.get("task_id", 0)),
        )
    except (TypeError, ValueError):
        logger.warning("worktree 元数据字段类型非法: %s", path)
        return None


def read_meta(root: str | Path, name: str) -> WorktreeMeta | None:
    path = meta_path(root, name)
    if not path.exists():
        return None
    return _parse(path)


def iter_metas(root: str | Path) -> list[WorktreeMeta]:
    directory = Path(root) / META_DIR_NAME
    if not directory.is_dir():
        return []
    out: list[WorktreeMeta] = []
    for path in sorted(directory.glob("*.json")):
        meta = _parse(path)
        if meta is not None:
            out.append(meta)
    return out


def delete_meta(root: str | Path, name: str) -> None:
    try:
        meta_path(root, name).unlink()
    except FileNotFoundError:
        pass
