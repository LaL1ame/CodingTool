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

    # —— 合并（state20 A3）——

    def add(self, path: Path, *paths: str) -> None:
        """暂存 path 工作区下的文件（缺省全部）。"""
        self.run(["add", "--", *paths] if paths else ["add", "."], cwd=path)

    def commit(self, path: Path, message: str) -> None:
        """在 path 工作区提交（需已 add）。"""
        self.run(["commit", "-m", message], cwd=path)

    def checkout_branch(self, path: Path, branch: str) -> None:
        """创建并切换到新分支（-b）；分支已存在时失败。"""
        self.run(["checkout", "-b", branch], cwd=path)

    def merge(self, path: Path, branch: str) -> bool:
        """把 branch 合并进 path 当前分支；返回是否有冲突。

        冲突时 `git merge` 退出码非 0（且工作区留有冲突标记），这里只负责
        报告冲突与否，不处理合并结果 —— 由调用方决定 abort。
        """
        cp = self._run(["merge", branch], cwd=path, check=False)
        return cp.returncode != 0

    def abort_merge(self, path: Path) -> None:
        """放弃进行中的合并，回到尝试前状态。"""
        self.run(["merge", "--abort"], cwd=path)

    def diff_names(self, path: Path, base: str) -> tuple[str, ...]:
        """base 与当前 HEAD 之间变更的文件名（冲突报告用）。"""
        cp = self._run(
            ["diff", "--name-only", f"{base}...HEAD"], cwd=path, check=False)
        if cp.returncode != 0:
            return ()
        return tuple(n for n in cp.stdout.splitlines() if n)

    def branch_created(self, branch: str) -> bool:
        """分支是否已存在（与 branch_exists 等价，语义更贴合并场景）。"""
        return self.branch_exists(branch)
