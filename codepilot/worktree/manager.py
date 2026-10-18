"""Worktree 生命周期编排：进入 / 退出 / 删除 / 列表 / 过期清理。

- 目录固定在主仓 ``.codepilot/worktrees/``（已被 gitignore）。
- 元数据独立存放在 ``_meta/``，快速恢复只读文件系统、不调用 Git。
- 进程内 RLock + 名称占用表；同名任务被拒绝。
- 所有 Git 调用经由 `GitRunner`，同步实现由异步调用方用 ``to_thread`` 卸载。
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import threading
import time
from pathlib import Path

from codepilot.worktree.git import GitRunner
from codepilot.worktree.initializer import Initializer
from codepilot.worktree.meta import delete_meta, iter_metas, read_meta, write_meta
from codepilot.worktree.model import (
    CleanupReport,
    ExitResult,
    RetentionDecision,
    WorktreeEntry,
    WorktreeHandle,
    WorktreeMeta,
)
from codepilot.worktree.naming import auto_name, validate_name
from codepilot.worktree.settings import WorktreeSettings

logger = logging.getLogger("codepilot.worktree")

WORKTREE_ROOT_PARTS = (".codepilot", "worktrees")
BRANCH_PREFIX = "codepilot/"


class WorktreeError(RuntimeError):
    """Worktree 生命周期操作失败（fail-closed，不退回主工作区）。"""


def decide_retention(*, dirty: bool, ahead: int) -> RetentionDecision:
    """无未提交改动且无新提交 → 清理；否则保留。"""
    if dirty:
        return RetentionDecision(True, "kept/dirty")
    if ahead > 0:
        return RetentionDecision(True, f"kept/ahead={ahead}")
    return RetentionDecision(False, "removed/clean")


def _same_path(a: Path, b: Path) -> bool:
    return os.path.normcase(str(a)) == os.path.normcase(str(b))


class WorktreeManager:
    def __init__(
        self,
        main_root: str | Path,
        settings: WorktreeSettings | None = None,
        git: GitRunner | None = None,
    ) -> None:
        self._main_root = Path(main_root).resolve()
        self._settings = settings or WorktreeSettings()
        self._git = git or GitRunner(self._main_root)
        self._root = self._main_root.joinpath(*WORKTREE_ROOT_PARTS)
        self._initializer = Initializer(self._main_root, self._settings, self._git)
        self._lock = threading.RLock()
        self._active: dict[str, int] = {}

    @property
    def root(self) -> Path:
        return self._root

    @property
    def settings(self) -> WorktreeSettings:
        return self._settings

    # —— 内部工具 ——
    def _ensure_repo_root(self) -> None:
        top = self._git.toplevel()
        if top is None:
            raise WorktreeError(f"不是 Git 仓库: {self._main_root}")
        if not _same_path(top, self._main_root):
            raise WorktreeError(
                f"Worktree 隔离要求从仓库根启动：当前工作区 {self._main_root}，"
                f"仓库根 {top}")

    def _safe_path(self, name: str) -> Path:
        root = self._root.resolve()
        path = (root / name).resolve()
        if not path.is_relative_to(root):
            raise WorktreeError(f"worktree 路径越界: {name!r}")
        return path

    def _managed_meta(self, path: Path, name: str) -> WorktreeMeta | None:
        """受管理目录三检：目录、``.git`` 文件、元数据（名字/分支前缀）。"""
        if not path.is_dir() or not (path / ".git").is_file():
            return None
        meta = read_meta(self._root, name)
        if meta is None or meta.name != name:
            return None
        if not meta.branch.startswith(BRANCH_PREFIX):
            return None
        return meta

    def _cleanup_failed(
        self, name: str, path: Path, branch: str, created_branch: bool,
    ) -> None:
        try:
            if path.exists():
                self._git.remove_worktree(path, force=True)
        except Exception as e:  # noqa: BLE001 - 清理尽力而为
            logger.warning("worktree 失败清理 remove 失败 %s: %s", path, e)
            try:
                shutil.rmtree(path, ignore_errors=True)
            except Exception:
                pass
        if created_branch:
            try:
                self._git.delete_branch(branch)
            except Exception as e:  # noqa: BLE001
                logger.warning("worktree 失败清理分支失败 %s: %s", branch, e)
        delete_meta(self._root, name)

    def _remove_dir_and_branch(
        self, name: str, path: Path, branch: str, *,
        delete_branch: bool, force: bool,
    ) -> None:
        self._git.remove_worktree(path, force=force)
        try:
            if delete_branch:
                self._git.delete_branch(branch)
        finally:
            delete_meta(self._root, name)

    # —— 进入 ——
    def enter(
        self, name: str | None, *, role: str, task_id: int,
    ) -> WorktreeHandle:
        with self._lock:
            resolved = validate_name(name) if name is not None \
                else auto_name(role, task_id)
            path = self._safe_path(resolved)
            owner = self._active.get(resolved)
            if owner is not None:
                raise WorktreeError(
                    f"worktree {resolved!r} 已被任务 #{owner} 占用，请换一个名字")
            self._active[resolved] = task_id
            try:
                # 快速恢复：只读文件系统，不调用 Git
                if path.exists():
                    meta = self._managed_meta(path, resolved)
                    if meta is None:
                        raise WorktreeError(
                            f"目录已存在但不是受管理的 worktree: {path}")
                    logger.info(
                        "worktree 复用 %s branch=%s (%s)",
                        resolved, meta.branch, path)
                    return WorktreeHandle(
                        name=resolved, path=path, branch=meta.branch,
                        base=meta.base, created_at=meta.created_at,
                        reused=True)

                self._ensure_repo_root()
                branch = f"{BRANCH_PREFIX}{resolved}"
                had_branch = self._git.branch_exists(branch)
                created_branch = not had_branch
                if had_branch:
                    old = read_meta(self._root, resolved)
                    if old is not None:
                        base, created_at = old.base, old.created_at
                    else:
                        base = self._git.merge_base("HEAD", branch) \
                            or self._git.head()
                        created_at = time.time()
                    self._git.attach_worktree(path, branch)
                else:
                    base = self._git.head()
                    created_at = time.time()
                    self._git.add_worktree(path, branch, base)

                logger.info(
                    "worktree %s %s branch=%s base=%s (%s)",
                    "挂接" if had_branch else "创建",
                    resolved, branch, base, path)
                meta = WorktreeMeta(
                    name=resolved, branch=branch, base=base,
                    created_at=created_at, role=role, task_id=task_id)
                write_meta(self._root, meta)
                try:
                    self._initializer.initialize(path)
                except Exception:
                    self._cleanup_failed(
                        resolved, path, branch, created_branch)
                    raise
                return WorktreeHandle(
                    name=resolved, path=path, branch=branch, base=base,
                    created_at=created_at, reused=False, attached=had_branch)
            except WorktreeError:
                self._active.pop(resolved, None)
                raise
            except Exception as e:
                self._active.pop(resolved, None)
                raise WorktreeError(
                    f"创建 worktree {resolved!r} 失败: {e}") from e

    # —— 退出 / 删除 ——
    def leave(self, handle: WorktreeHandle) -> ExitResult:
        with self._lock:
            try:
                dirty = bool(self._git.status_porcelain(handle.path))
                ahead = self._git.commits_ahead(handle.base, cwd=handle.path)
                decision = decide_retention(dirty=dirty, ahead=ahead)
                if decision.keep:
                    logger.info(
                        "worktree 保留 %s: %s", handle.name, decision.reason)
                    return ExitResult(
                        True, decision.reason, handle.path, handle.branch)
                self._remove_dir_and_branch(
                    handle.name, handle.path, handle.branch,
                    delete_branch=True, force=False)
                logger.info(
                    "worktree 清理 %s: %s", handle.name, decision.reason)
                return ExitResult(
                    False, decision.reason, handle.path, handle.branch)
            except Exception as e:  # noqa: BLE001 - 清理失败不掩盖任务结果
                logger.warning(
                    "worktree leave 失败 %s，保留目录: %s", handle.name, e)
                return ExitResult(
                    True, f"cleanup-failed: {e}", handle.path, handle.branch)
            finally:
                self._active.pop(handle.name, None)

    def release(self, handle: WorktreeHandle) -> None:
        """释放占用锁但不做保留/删除判定；目录与分支保持不变。

        state19 团队成员在 idle/blocked/running 期间长期占用 worktree；
        终止、失败或进程退出时用本方法释放名称锁，供后续重新派生复用。
        """
        with self._lock:
            self._active.pop(handle.name, None)

    def remove(self, name: str, *, force: bool = False) -> ExitResult:
        with self._lock:
            resolved = validate_name(name)
            path = self._safe_path(resolved)
            owner = self._active.get(resolved)
            if owner is not None:
                raise WorktreeError(
                    f"worktree {resolved!r} 正被任务 #{owner} 使用，无法删除")
            meta = self._managed_meta(path, resolved)
            if meta is None:
                raise WorktreeError(
                    f"不存在或不是受管理的 worktree: {resolved!r}")
            dirty = bool(self._git.status_porcelain(path))
            ahead = self._git.commits_ahead(meta.base, cwd=path)
            if not force and (dirty or ahead > 0):
                raise WorktreeError(
                    f"worktree {resolved!r} 有未提交改动或新提交"
                    f"（dirty={dirty}, ahead={ahead}）；确认后使用 --force"
                    f"（分支会保留）")
            self._git.remove_worktree(path, force=force)
            if not force:
                self._git.delete_branch(meta.branch)
            delete_meta(self._root, resolved)
            logger.info(
                "worktree 删除 %s force=%s branch=%s",
                resolved, force, meta.branch)
            reason = "removed/forced-branch-kept" if force else "removed/clean"
            return ExitResult(False, reason, path, meta.branch)

    # —— 查询 ——
    def list_status(self) -> list[WorktreeEntry]:
        with self._lock:
            out: list[WorktreeEntry] = []
            for meta in iter_metas(self._root):
                try:
                    name = validate_name(meta.name)
                    path = self._safe_path(name)
                except Exception:  # noqa: BLE001 - 坏元数据跳过
                    continue
                if not path.is_dir():
                    continue
                try:
                    dirty = bool(self._git.status_porcelain(path))
                    ahead = self._git.commits_ahead(meta.base, cwd=path)
                except Exception:  # noqa: BLE001 - 状态不可读时保守显示
                    dirty, ahead = True, 0
                out.append(WorktreeEntry(
                    name=name, path=path, branch=meta.branch, base=meta.base,
                    created_at=meta.created_at, dirty=dirty, ahead=ahead,
                    locked_by=self._active.get(name)))
            out.sort(key=lambda e: e.created_at)
            return out

    # —— 过期清理 ——
    def cleanup_expired(self, *, now: float | None = None) -> CleanupReport:
        with self._lock:
            current = time.time() if now is None else now
            ttl_seconds = self._settings.ttl_hours * 3600
            removed: list[str] = []
            skipped: list[tuple[str, str]] = []
            for meta in iter_metas(self._root):
                try:
                    name = validate_name(meta.name)
                except Exception as e:  # noqa: BLE001
                    skipped.append((str(meta.name), f"invalid-name: {e}"))
                    continue
                if meta.created_at > current - ttl_seconds:
                    continue
                if name in self._active:
                    skipped.append((name, "占用中"))
                    continue
                try:
                    path = self._safe_path(name)
                    managed = self._managed_meta(path, name)
                    if managed is None:
                        skipped.append((name, "非受管理目录"))
                        continue
                    dirty = bool(self._git.status_porcelain(path))
                    ahead = self._git.commits_ahead(managed.base, cwd=path)
                    if dirty or ahead > 0:
                        skipped.append(
                            (name, f"有变更(dirty={dirty}, ahead={ahead})"))
                        continue
                    self._remove_dir_and_branch(
                        name, path, managed.branch,
                        delete_branch=True, force=False)
                    removed.append(name)
                    logger.info("worktree 过期清理删除 %s", name)
                except Exception as e:  # noqa: BLE001 - 单项失败不中断
                    skipped.append((name, f"error: {e}"))
                    logger.info("worktree 过期清理跳过 %s: %s", name, e)
            return CleanupReport(tuple(removed), tuple(skipped))

    async def run_periodic_cleanup(self) -> None:
        """立即清理一次，之后按配置周期循环；取消时退出。"""
        interval = max(1.0, self._settings.cleanup_interval_minutes * 60)
        while True:
            try:
                report = await asyncio.to_thread(self.cleanup_expired)
                if report.removed:
                    logger.info("worktree 过期清理删除: %s",
                                ", ".join(report.removed))
                for name, reason in report.skipped:
                    logger.info("worktree 清理跳过 %s: %s", name, reason)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("worktree 过期清理失败")
            await asyncio.sleep(interval)
