"""Worktree 环境初始化：copy / include / symlink / Git hooks。

规则来源是 `WorktreeSettings`。失败分级：
- 源不存在 / glob 无匹配 → 跳过并记日志；
- 规则路径逃逸主仓、复制/链接 IO 失败 → 抛 `WorktreeInitError`。
"""

from __future__ import annotations

import glob as globmod
import logging
import os
import shutil
import subprocess
from pathlib import Path

from codepilot.worktree.git import GitError, GitRunner
from codepilot.worktree.settings import WorktreeSettings

logger = logging.getLogger("codepilot.worktree")


class WorktreeInitError(RuntimeError):
    """环境初始化失败。"""


class Initializer:
    def __init__(
        self, main_root: str | Path, settings: WorktreeSettings,
        git: GitRunner,
    ) -> None:
        self.main_root = Path(main_root).resolve()
        self.settings = settings
        self.git = git

    def initialize(self, wt_path: str | Path) -> None:
        wt = Path(wt_path)
        for pattern in self.settings.copy:
            self._copy_rules(wt, pattern, include_only=False)
        for pattern in self.settings.include:
            self._copy_rules(wt, pattern, include_only=True)
        for pattern in self.settings.symlink:
            self._symlink_rules(wt, pattern)
        self._configure_hooks(wt)

    # —— 规则展开与安全校验 ——
    def _expand(self, pattern: str) -> list[Path]:
        matches = globmod.glob(str(self.main_root / pattern), recursive=True)
        out: list[Path] = []
        for raw in matches:
            src = Path(raw)
            try:
                resolved = src.resolve()
            except OSError:
                continue
            if not resolved.is_relative_to(self.main_root):
                raise WorktreeInitError(
                    f"初始化规则越出主仓: {pattern!r} -> {resolved}")
            if resolved == self.main_root:
                raise WorktreeInitError(f"初始化规则不能指向主仓根: {pattern!r}")
            out.append(src)
        return out

    def _dest(self, wt: Path, src: Path) -> Path:
        try:
            rel = src.resolve().relative_to(self.main_root)
        except ValueError as e:
            raise WorktreeInitError(
                f"初始化源路径不在主仓内: {src}") from e
        return wt / rel

    # —— copy / include ——
    def _copy_rules(self, wt: Path, pattern: str, *, include_only: bool) -> None:
        for src in self._expand(pattern):
            if include_only and not self.git.is_ignored(src):
                continue
            dest = self._dest(wt, src)
            if dest.exists() or dest.is_symlink():
                continue
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                if src.is_dir():
                    shutil.copytree(src, dest, symlinks=False)
                else:
                    shutil.copy2(src, dest)
            except OSError as e:
                raise WorktreeInitError(f"复制失败 {src} -> {dest}: {e}") from e

    # —— symlink ——
    def _symlink_rules(self, wt: Path, pattern: str) -> None:
        for src in self._expand(pattern):
            if not src.is_dir():
                logger.info("worktree symlink 跳过非目录: %s", src)
                continue
            dest = self._dest(wt, src)
            if dest.exists() or dest.is_symlink():
                continue
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                self._link_dir(src.resolve(), dest)
            except OSError as e:
                raise WorktreeInitError(
                    f"创建目录链接失败 {src} -> {dest}: {e}") from e

    @staticmethod
    def _link_dir(src: Path, dest: Path) -> None:
        try:
            os.symlink(src, dest, target_is_directory=True)
            return
        except OSError as e:
            if os.name != "nt":
                raise
            logger.info("symlink 失败，回退 junction: %s (%s)", dest, e)
        cp = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(dest), str(src)],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", shell=False,
        )
        if cp.returncode != 0:
            raise OSError(
                f"mklink /J 失败({cp.returncode}): "
                f"{(cp.stderr or cp.stdout).strip()}")

    # —— Git hooks ——
    def _configure_hooks(self, wt: Path) -> None:
        githooks = self.main_root / ".githooks"
        try:
            if githooks.is_dir():
                link = wt / ".githooks"
                if not link.exists() and not link.is_symlink():
                    self._link_dir(githooks.resolve(), link)
                self.git.set_worktree_config(
                    wt, "core.hooksPath", str(githooks.resolve()))
                return
            hooks_path = self.git.hooks_path()
            if hooks_path and not os.path.isabs(hooks_path):
                absolute = (self.main_root / hooks_path).resolve()
                self.git.set_worktree_config(
                    wt, "core.hooksPath", str(absolute))
        except GitError as e:
            raise WorktreeInitError(f"Git hooks 配置失败: {e}") from e
