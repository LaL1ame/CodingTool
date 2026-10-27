"""state20 端到端：跨进程恢复 + 合并 + 冲突回滚（临时 Git 仓库）。"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from codepilot.protocols import Delta
from codepilot.subagents.model import AgentRole
from codepilot.teams.manager import TeamManager
from codepilot.teams.model import TeamSettings
from codepilot.tools.registry import ToolRegistry
from codepilot.worktree.manager import WorktreeManager
from tests.teams.conftest import FakeAgent


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True,
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace")


@pytest.fixture
def git_repo(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git 不可用")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@e.com")
    _git(repo, "config", "user.name", "T")
    (repo / ".gitignore").write_text(".codepilot/\n", encoding="utf-8")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")
    return repo


def _roles() -> dict[str, AgentRole]:
    return {"coder": AgentRole(
        name="coder", description="coder", body="", tools=frozenset())}


def _manager(settings: TeamSettings, repo: Path) -> TeamManager:
    def factory(**kw):
        return FakeAgent([[Delta(text="成员完成")]])
    return TeamManager(
        settings, workspace=repo, base_registry=ToolRegistry(),
        roles=_roles(),
        provider=SimpleNamespace(config=SimpleNamespace(model="m")),
        worktree=WorktreeManager(repo),
        agent_factory=factory)


def _main_branch(repo):
    out = subprocess.run(["git", "branch", "--show-current"], cwd=repo,
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


async def test_restore_then_merge_e2e(
    settings: TeamSettings, git_repo: Path,
):
    """场景 A+B：派生→对话落盘→模拟重启恢复→改文件→合并到汇聚分支。"""
    main_branch = _main_branch(git_repo)
    # —— 第一次会话：派生成员，触发对话落盘 ——
    mgr1 = _manager(settings, git_repo)
    mgr1.create_team("alpha")
    await mgr1.spawn_member(name="alice", role="coder")
    runtime = mgr1._runtimes["alice"]
    # 手动触发一次 agent 回合 → _msgs 增长 → 落盘
    await runtime._run_agent("执行任务 A")
    assert len(runtime._msgs) > 0
    ctx_file = settings.root / "alpha" / "context" / "alice.jsonl"
    assert ctx_file.exists(), "成员上下文应落盘"
    await mgr1.shutdown()

    # —— 模拟重启：重建 manager（同 worktree，成员 offline）——
    mgr2 = _manager(settings, git_repo)
    mgr2.load_current()
    await mgr2.spawn_member(name="alice", role="coder")
    restored = mgr2._runtimes["alice"]
    assert len(restored._msgs) == len(runtime._msgs), "应恢复历史"
    # 成员改文件
    wt = restored._member.worktree_path
    (wt / "alice.txt").write_text("alice content\n", encoding="utf-8")
    await mgr2.shutdown()

    # —— 合并：manager.merge_team 门面 ——
    mgr3 = _manager(settings, git_repo)
    mgr3.load_current()
    result = await mgr3.merge_team()
    assert result.ok
    assert result.target == "codepilot/team/alpha/merged"
    # 汇聚分支含 alice.txt；主分支不含
    _git(git_repo, "checkout", "codepilot/team/alpha/merged")
    assert (git_repo / "alice.txt").exists()
    _git(git_repo, "checkout", main_branch)
    assert not (git_repo / "alice.txt").exists(), "主分支不应被污染"
    await mgr3.shutdown()


async def test_merge_conflict_e2e(
    settings: TeamSettings, git_repo: Path,
):
    """场景 C：两成员改同一文件 → 冲突回滚。"""
    main_branch = _main_branch(git_repo)
    mgr = _manager(settings, git_repo)
    mgr.create_team("alpha")
    await mgr.spawn_member(name="alice", role="coder")
    await mgr.spawn_member(name="bob", role="coder")
    # 两个成员各自改 base.txt（不同内容）—— 直接写 worktree，不 commit（state19 语义）
    alice_wt = mgr._runtimes["alice"]._member.worktree_path
    bob_wt = mgr._runtimes["bob"]._member.worktree_path
    (alice_wt / "base.txt").write_text("alice\n", encoding="utf-8")
    (bob_wt / "base.txt").write_text("bob\n", encoding="utf-8")

    result = await mgr.merge_team()
    assert result.ok is False
    assert "base.txt" in result.conflict_files
    # 主分支回到干净 base
    _git(git_repo, "checkout", main_branch)
    assert (git_repo / "base.txt").read_text(encoding="utf-8") == "base\n"
    await mgr.shutdown()
