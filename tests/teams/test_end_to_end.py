"""临时 Git 仓库端到端：建组→任务→派生→审批→完成→报告。"""

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
from tests.teams.conftest import wait_until


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True,
        text=True, encoding="utf-8", errors="replace")
    return out.stdout


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    if shutil.which("git") is None:
        pytest.skip("git 不可用")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / ".gitignore").write_text(".codepilot/\n", encoding="utf-8")
    (repo / "a.txt").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")
    return repo


class SmartAgent:
    """按提示词阶段返回文本；不调用真实模型、不写文件。"""

    def __init__(self, name: str):
        self.name = name

    async def run(self, messages):
        prompt = str(messages[-1].get("content", ""))
        if "制定执行计划" in prompt:
            yield Delta(text=f"{self.name} 的计划")
        elif "请执行共享任务" in prompt:
            yield Delta(text=f"{self.name} 完成")
        else:
            yield Delta(text="收到")


def _manager(
    settings: TeamSettings, repo: Path,
) -> TeamManager:
    def factory(**kw):
        return SmartAgent(kw["workspace"].name)

    return TeamManager(
        settings,
        workspace=repo,
        base_registry=ToolRegistry(),
        roles={"coder": AgentRole(
            name="coder", description="coder", body="", tools=frozenset())},
        provider=SimpleNamespace(config=SimpleNamespace(model="m")),
        worktree=WorktreeManager(repo),
        agent_factory=factory,
    )


async def test_full_team_flow_in_temp_git_repo(
    settings: TeamSettings, git_repo: Path,
):
    mgr = _manager(settings, git_repo)
    mgr.create_team("alpha")

    alice = await mgr.spawn_member(name="alice", role="coder")
    bob = await mgr.spawn_member(
        name="bob", role="coder", requires_approval=True)
    assert alice.status.value == "idle" and bob.status.value == "idle"

    a = mgr.create_task(title="任务 A", assignee="alice")
    await wait_until(
        lambda: mgr.tasks().get(a.id).status.value == "done")

    b = mgr.create_task(
        title="任务 B", depends_on=[a.id], assignee="bob")
    await wait_until(
        lambda: b.id in mgr._pending_approvals)
    assert mgr.tasks().get(b.id).status.value == "pending"
    decision = mgr.decide(task_id=b.id, approved=True, reason="可以")
    assert decision.approved is True
    await wait_until(
        lambda: mgr.tasks().get(b.id).status.value == "done")

    counts = mgr.task_counts()
    assert counts["done"] == 2
    for name in ("alice", "bob"):
        ref = mgr.registry().resolve(name)
        assert ref.worktree_path.is_dir()
        # 模拟成员产物；不经过 merge，主工作区不应出现。
        (ref.worktree_path / f"{name}.txt").write_text(
            name, encoding="utf-8")
    await mgr.shutdown()

    for name in ("alice", "bob"):
        ref = mgr.registry().resolve(name)
        assert ref.worktree_path.is_dir()
        assert (ref.worktree_path / f"{name}.txt").exists()
    branches = _git(git_repo, "branch", "--list", "codepilot/team/alpha/*")
    assert "alice" in branches and "bob" in branches
    assert not (git_repo / "alice.txt").exists()
    assert not (git_repo / "bob.txt").exists()
    assert "团队" not in (git_repo / "a.txt").read_text(encoding="utf-8")
