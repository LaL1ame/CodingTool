"""A3 成员成果合并：把成员分支改动汇总到汇聚分支，冲突则安全回滚。

流程（全同步，调用方用 asyncio.to_thread 卸载）：
1. 逐个成员：在成员 worktree 内 `git add .` + `git commit`（有未提交改动才提交）。
2. 在主工作区确保汇聚分支 `codepilot/team/<team>/merged` 存在并检出。
3. 逐个 `git merge <member-branch>`；冲突 → `merge --abort` + 冲突文件清单 → 回滚报告。
4. 全部合完 → MergeResult。

只在汇聚分支操作，成员分支与主工作区当前分支不被 checkout/merge 污染。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from codepilot.worktree.git import GitRunner
from codepilot.teams.model import MemberRef, TeamMemberStatus

logger = logging.getLogger("codepilot.teams.merge")

_ACTIVE = {
    TeamMemberStatus.SPAWNING, TeamMemberStatus.RUNNING,
    TeamMemberStatus.IDLE, TeamMemberStatus.BLOCKED,
    TeamMemberStatus.OFFLINE,
}


def merge_branch_name(team: str) -> str:
    """固定汇聚分支名。"""
    return f"codepilot/team/{team}/merged"


@dataclass(frozen=True)
class MemberChanges:
    member: str
    branch: str
    dirty: bool
    ahead: int
    committed: bool


@dataclass(frozen=True)
class MergeResult:
    ok: bool
    target: str
    merged_members: tuple[str, ...]
    committed_members: tuple[str, ...]
    conflict_files: tuple[str, ...] = ()
    error: str = ""


class TeamMerger:
    def __init__(self, workspace, git: GitRunner) -> None:
        self.workspace = workspace
        self._git = git

    def commit_member_changes(self, ref: MemberRef) -> MemberChanges:
        """把成员 worktree 的未提交改动提交到成员分支；无改动不提交。"""
        wt = ref.worktree_path
        dirty = bool(self._git.status_porcelain(wt))
        ahead = self._git.commits_ahead("HEAD", cwd=wt) if dirty else 0
        committed = False
        if dirty:
            self._git.add(wt)
            msg = f"team {ref.name}: {time.strftime('%Y-%m-%d %H:%M:%S')} 成员改动"
            self._git.commit(wt, msg)
            committed = True
        return MemberChanges(
            member=ref.name, branch=ref.branch, dirty=dirty,
            ahead=ahead, committed=committed)

    def _ensure_target(self, team: str, target: str) -> None:
        """确保汇聚分支存在于主工作区并检出。"""
        if self._git.branch_created(target):
            self._git.run(["checkout", target], cwd=self.workspace)
        else:
            self._git.checkout_branch(self.workspace, target)

    def _conflict_files(self, path) -> tuple[str, ...]:
        """从 git status --porcelain 提取未合并(冲突)文件。"""
        out = self._git.status_porcelain(path)
        files = []
        for line in out.splitlines():
            if not line or len(line) < 3:
                continue
            # 冲突行形如 "UU path"
            if line[0] == "U" or line[1] == "U":
                files.append(line[3:].strip())
        return tuple(files)

    def merge_members(
        self, team: str, members: list[MemberRef], *, base: str = "HEAD",
    ) -> MergeResult:
        """合并成员分支到汇聚分支；冲突回滚并上报。"""
        target = merge_branch_name(team)
        committed: list[str] = []
        merged: list[str] = []
        # 1. 成员改动先 commit 到各自分支
        try:
            for ref in members:
                changes = self.commit_member_changes(ref)
                if changes.committed:
                    committed.append(ref.name)
        except Exception as e:  # noqa: BLE001 - 提交失败不中断整体
            logger.warning("成员提交失败: %s", e)
        # 2. 汇聚分支
        try:
            self._ensure_target(team, target)
        except Exception as e:  # noqa: BLE001
            return MergeResult(
                ok=False, target=target, merged_members=(),
                committed_members=tuple(committed),
                error=f"创建/检出汇聚分支失败: {e}")
        # 3. 逐个 merge
        for ref in members:
            conflicted = self._git.merge(self.workspace, ref.branch)
            if conflicted:
                files = self._conflict_files(self.workspace)
                try:
                    self._git.abort_merge(self.workspace)
                except Exception as e:  # noqa: BLE001
                    logger.warning("abort 失败: %s", e)
                return MergeResult(
                    ok=False, target=target, merged_members=tuple(merged),
                    committed_members=tuple(committed),
                    conflict_files=files,
                    error=f"合并 {ref.branch} 冲突，已回滚")
            merged.append(ref.name)
        return MergeResult(
            ok=True, target=target, merged_members=tuple(merged),
            committed_members=tuple(committed))


def select_mergeable(members: list[MemberRef]) -> list[MemberRef]:
    """过滤可合并成员（排除 lead 与 terminated/failed）。"""
    return [m for m in members
            if m.name != "lead" and m.status in _ACTIVE]
