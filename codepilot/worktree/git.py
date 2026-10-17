"""Git 子进程封装的唯一出口。

所有命令使用参数列表、``shell=False``、显式 ``cwd``；非零退出统一抛
`GitError`（含命令与 stderr），调用方无需解析输出。
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path


class GitError(RuntimeError):
    """Git 命令执行失败。"""


class GitRunner:
    """在一个主仓库上执行 Git 命令；主仓库根显式持有，不依赖进程 cwd。"""

    def __init__(self, main_root: str | Path) -> None:
        self.main_root = Path(main_root).resolve()

    def _run(
        self, args: Sequence[str], *, cwd: Path | None = None, check: bool = True,
    ) -> subprocess.CompletedProcess:
        cmd = ["git", *args]
        try:
            cp = subprocess.run(
                cmd,
                cwd=str(cwd or self.main_root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
            )
        except OSError as e:
            raise GitError(f"无法执行 git: {e}") from e
        if check and cp.returncode != 0:
            detail = (cp.stderr or cp.stdout).strip()
            raise GitError(
                f"git {' '.join(args)} 失败({cp.returncode}): {detail}")
        return cp

    def run(
        self, args: Sequence[str], *, cwd: Path | None = None, check: bool = True,
    ) -> str:
        return self._run(args, cwd=cwd, check=check).stdout.strip()

    # —— 仓库与提交 ——
    def toplevel(self) -> Path | None:
        cp = self._run(["rev-parse", "--show-toplevel"], check=False)
        if cp.returncode != 0:
            return None
        out = cp.stdout.strip()
        return Path(out).resolve() if out else None

    def head(self) -> str:
        return self.run(["rev-parse", "HEAD"])

    def branch_exists(self, branch: str) -> bool:
        cp = self._run(
            ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
            check=False)
        return cp.returncode == 0

    # —— Worktree ——
    def add_worktree(self, path: Path, branch: str, base: str) -> None:
        self.run(["worktree", "add", "-b", branch, str(path), base])

    def attach_worktree(self, path: Path, branch: str) -> None:
        self.run(["worktree", "add", str(path), branch])

    def remove_worktree(self, path: Path, force: bool = False) -> None:
        args = ["worktree", "remove"]
        if force:
            args.append("--force")
        args.append(str(path))
        self.run(args)

    def delete_branch(self, branch: str) -> None:
        self.run(["branch", "-D", branch])

    # —— 状态 ——
    def status_porcelain(self, path: Path) -> str:
        return self.run(["status", "--porcelain"], cwd=path)

    def commits_ahead(
        self, base: str, rev: str = "HEAD", *, cwd: Path | None = None,
    ) -> int:
        out = self.run(
            ["rev-list", "--count", f"{base}..{rev}"], cwd=cwd or self.main_root)
        return int(out or 0)

    def merge_base(
        self, a: str, b: str, *, cwd: Path | None = None,
    ) -> str | None:
        cp = self._run(
            ["merge-base", a, b], cwd=cwd or self.main_root, check=False)
        if cp.returncode != 0:
            return None
        return cp.stdout.strip() or None

    def is_ignored(self, path: Path) -> bool:
        try:
            rel = Path(path).resolve().relative_to(self.main_root)
        except ValueError:
            return False
        cp = self._run(["check-ignore", "-q", "--", str(rel)], check=False)
        return cp.returncode == 0

    # —— 配置 ——
    def hooks_path(self) -> str | None:
        cp = self._run(["config", "--get", "core.hooksPath"], check=False)
        if cp.returncode != 0:
            return None
        return cp.stdout.strip() or None

    def set_worktree_config(self, path: Path, key: str, value: str) -> None:
        """启用 worktreeConfig 并写入 worktree 级配置。"""
        self.run(["config", "extensions.worktreeConfig", "true"])
        self.run(["config", "--worktree", key, value], cwd=path)
