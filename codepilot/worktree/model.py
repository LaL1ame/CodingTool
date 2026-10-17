"""Worktree 领域数据模型。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorktreeMeta:
    """一个 worktree 的持久化元数据（存于 ``_meta/<sha1(name)>.json``）。"""

    name: str
    branch: str
    base: str          # 创建/挂接时记录的基线 commit
    created_at: float
    role: str | None = None
    task_id: int = 0


@dataclass(frozen=True)
class WorktreeHandle:
    """一次 enter 的返回值：子 Agent 绑定此目录运行。"""

    name: str
    path: Path
    branch: str
    base: str
    created_at: float
    reused: bool = False       # 目录已存在、走快速恢复
    attached: bool = False     # 挂到已有分支


@dataclass(frozen=True)
class WorktreeEntry:
    """列表/状态查询的一项。"""

    name: str
    path: Path
    branch: str
    base: str
    created_at: float
    dirty: bool
    ahead: int
    locked_by: int | None = None


@dataclass(frozen=True)
class ExitResult:
    """leave / remove 的结果。"""

    kept: bool
    reason: str
    path: Path
    branch: str


@dataclass(frozen=True)
class CleanupReport:
    """过期清理报告。"""

    removed: tuple[str, ...] = ()
    skipped: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class RetentionDecision:
    """保留/清理判定（纯数据，便于无 IO 单测）。"""

    keep: bool
    reason: str
