"""TeamManager：小组/成员生命周期、消息门面、事件与任务门面。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from codepilot.protocols import Delta
from codepilot.subagents.model import AgentRole
from codepilot.teams.manager import TeamManager
from codepilot.teams.model import (
    TeamBackendError,
    TeamMemberError,
    TeamMemberStatus,
    TeamSettings,
)
from codepilot.teams.tools_lead import build_lead_tools
from codepilot.tools.registry import ToolRegistry
from codepilot.worktree.model import WorktreeHandle
from tests.teams.conftest import FakeAgent


class FakeWorktree:
    def __init__(self, root: Path):
        self.root = root
        self.entered: list[str] = []
        self.released: list[str] = []
        self.left: list[str] = []
        self._seen: set[str] = set()

    def enter(self, name, *, role, task_id):
        self.entered.append(name)
        path = self.root / name.replace("/", "__")
        path.mkdir(parents=True, exist_ok=True)
        reused = name in self._seen
        self._seen.add(name)
        return WorktreeHandle(
            name=name, path=path, branch=f"codepilot/{name}",
            base="base", created_at=1.0, reused=reused)

    def release(self, handle):
        self.released.append(handle.name)

    def leave(self, handle):
        self.left.append(handle.name)


def _roles() -> dict[str, AgentRole]:
    return {
        "coder": AgentRole(
            name="coder", description="coder", body="你是编码成员",
            tools=frozenset()),
    }


def _manager(
    settings: TeamSettings, tmp_path: Path, worktree,
    *, agent_factory=None,
) -> TeamManager:
    workspace = tmp_path / "main"
    workspace.mkdir(exist_ok=True)
    if agent_factory is None:
        def agent_factory(**kw):
            return FakeAgent([[Delta(text="ok")]])
    provider = SimpleNamespace(config=SimpleNamespace(model="m"))
    return TeamManager(
        settings, workspace=workspace,
        base_registry=ToolRegistry(), roles=_roles(),
        provider=provider, worktree=worktree,
        agent_factory=agent_factory)


def test_team_lifecycle(
    settings: TeamSettings, tmp_path: Path,
):
    worktree = FakeWorktree(tmp_path / "wt")
    mgr = _manager(settings, tmp_path, worktree)
    a = mgr.create_team("alpha")
    b = mgr.create_team("beta")
    assert a.name == "alpha" and b.name == "beta"
    assert mgr.team_name == "beta"
    assert mgr.list_teams() == ["alpha", "beta"]
    mgr.use_team("alpha")
    assert mgr.current_team().name == "alpha"
    with pytest.raises(TeamMemberError):
        mgr.create_team("alpha")
    with pytest.raises(TeamMemberError):
        mgr.use_team("missing")


async def test_team_persists_and_members_offline(
    settings: TeamSettings, tmp_path: Path,
):
    worktree = FakeWorktree(tmp_path / "wt")
    mgr = _manager(settings, tmp_path, worktree)
    mgr.create_team("alpha")
    mgr.registry().add_member(_member(tmp_path, "alice"))
    await mgr.shutdown()

    mgr2 = _manager(settings, tmp_path, worktree)
    meta = mgr2.load_current()
    assert meta.name == "alpha"
    assert mgr2.registry().resolve("alice").status is TeamMemberStatus.OFFLINE


async def test_spawn_and_terminate(
    settings: TeamSettings, tmp_path: Path,
):
    worktree = FakeWorktree(tmp_path / "wt")
    mgr = _manager(settings, tmp_path, worktree)
    mgr.create_team("alpha")
    member = await mgr.spawn_member(name="alice", role="coder")
    assert member.status is TeamMemberStatus.IDLE
    assert worktree.entered == ["team/alpha/alice"]
    ref = mgr.registry().resolve("alice")
    assert ref.branch == "codepilot/team/alpha/alice"

    terminated = await mgr.terminate_member("alice", reason="done")
    assert terminated.status is TeamMemberStatus.TERMINATED
    assert worktree.released == ["team/alpha/alice"]
    with pytest.raises(TeamMemberError):
        await mgr.terminate_member("alice")
    with pytest.raises(TeamMemberError):
        await mgr.spawn_member(name="alice", role="coder")
    await mgr.shutdown()


async def test_spawn_unknown_role_and_backend(
    settings: TeamSettings, tmp_path: Path,
):
    worktree = FakeWorktree(tmp_path / "wt")
    mgr = _manager(settings, tmp_path, worktree)
    mgr.create_team("alpha")
    with pytest.raises(TeamMemberError):
        await mgr.spawn_member(name="alice", role="ghost")
    with pytest.raises(TeamBackendError):
        await mgr.spawn_member(
            name="alice", role="coder", backend="terminal")
    await mgr.shutdown()


async def test_spawn_failure_rolls_back(
    settings: TeamSettings, tmp_path: Path,
):
    worktree = FakeWorktree(tmp_path / "wt")

    def bad_factory(**kw):
        raise RuntimeError("agent build failed")

    mgr = _manager(settings, tmp_path, worktree, agent_factory=bad_factory)
    mgr.create_team("alpha")
    with pytest.raises(TeamMemberError):
        await mgr.spawn_member(name="alice", role="coder")
    assert mgr.registry().list() == []
    assert worktree.left == ["team/alpha/alice"]
    await mgr.shutdown()


async def test_offline_member_can_be_revived(
    settings: TeamSettings, tmp_path: Path,
):
    worktree = FakeWorktree(tmp_path / "wt")
    mgr = _manager(settings, tmp_path, worktree)
    mgr.create_team("alpha")
    await mgr.spawn_member(name="alice", role="coder")
    await mgr.shutdown()

    mgr2 = _manager(settings, tmp_path, worktree)
    mgr2.load_current()
    assert mgr2.registry().resolve("alice").status is TeamMemberStatus.OFFLINE
    revived = await mgr2.spawn_member(name="alice", role="coder")
    assert revived.status is TeamMemberStatus.IDLE
    assert worktree.entered[-1] == "team/alpha/alice"
    await mgr2.shutdown()


async def test_switch_team_marks_old_members_offline(
    settings: TeamSettings, tmp_path: Path,
):
    worktree = FakeWorktree(tmp_path / "wt")
    mgr = _manager(settings, tmp_path, worktree)
    mgr.create_team("alpha")
    await mgr.spawn_member(name="alice", role="coder")
    mgr.create_team("beta")
    mgr.use_team("alpha")
    assert mgr.registry().resolve("alice").status is TeamMemberStatus.OFFLINE
    await mgr.shutdown()


async def test_messages_events_and_inbox(
    settings: TeamSettings, tmp_path: Path,
):
    worktree = FakeWorktree(tmp_path / "wt")
    mgr = _manager(settings, tmp_path, worktree)
    mgr.create_team("alpha")
    mgr.registry().add_member(_member(tmp_path, "alice"))
    events = []
    mgr.set_on_event(events.append)

    sent = mgr.send(
        sender="lead", recipient="alice", type="text", body="hi")
    assert sent.recipient == "alice"
    inbox = mgr._mailbox("alice").peek()
    assert any(m.body == "hi" for m in inbox)

    reply = mgr.send(
        sender="alice", recipient="lead", type="text", body="hello")
    assert reply.recipient == "lead"
    assert any(e.member == "alice" for e in events)
    got = mgr.lead_inbox(limit=10)
    assert any(m.body == "hello" for m in got)

    delivered, failures = mgr.broadcast(sender="lead", body="all")
    assert failures == []
    assert [m.recipient for m in delivered] == ["alice"]
    await mgr.shutdown()


async def test_status_and_decision_flow(
    settings: TeamSettings, tmp_path: Path,
):
    worktree = FakeWorktree(tmp_path / "wt")
    mgr = _manager(settings, tmp_path, worktree)
    mgr.create_team("alpha")
    mgr.registry().add_member(_member(tmp_path, "alice"))
    task = mgr.create_task(title="A", assignee="alice")

    mgr.send(
        sender="alice", recipient="lead", type="plan_request",
        body="plan", task_id=task.id)
    assert task.id in mgr._pending_approvals
    # 设计补充 C：有待审批时，Lead 上下文注入计划摘要。
    ctx = mgr.lead_context()
    assert "待审批计划" in ctx and f"#{task.id}" in ctx
    decision = mgr.decide(task_id=task.id, approved=True, reason="ok")
    assert decision.approved is True
    assert task.id not in mgr._pending_approvals
    assert mgr.registry().resolve("alice").status is TeamMemberStatus.IDLE
    await mgr.shutdown()


async def test_lead_tools_are_built(settings: TeamSettings, tmp_path: Path):
    worktree = FakeWorktree(tmp_path / "wt")
    mgr = _manager(settings, tmp_path, worktree)
    names = {t.name for t in build_lead_tools(mgr)}
    from codepilot.teams.tools_lead import LEAD_TOOL_NAMES

    assert names == LEAD_TOOL_NAMES


def _member(tmp_path: Path, name: str):
    from codepilot.teams.model import (
        TeamMember, TeamMemberBackend,
    )

    return TeamMember(
        name=name, role="coder", worktree_path=tmp_path / f"wt-{name}",
        branch=f"codepilot/team/alpha/{name}",
        backend=TeamMemberBackend.INPROCESS,
        requires_approval=False, status=TeamMemberStatus.IDLE,
        created_at=1.0, updated_at=1.0)


async def test_spawn_builds_real_agent(
    settings: TeamSettings, tmp_path: Path,
):
    """不使用 agent_factory 时，_build_runtime 应能构造真实 Agent。"""
    worktree = FakeWorktree(tmp_path / "wt")
    workspace = tmp_path / "main"
    workspace.mkdir(exist_ok=True)
    mgr = TeamManager(
        settings, workspace=workspace,
        base_registry=ToolRegistry(), roles=_roles(),
        provider=SimpleNamespace(config=SimpleNamespace(model="m")),
        worktree=worktree)
    mgr.create_team("alpha")
    member = await mgr.spawn_member(name="alice", role="coder")
    assert member.status is TeamMemberStatus.IDLE
    await mgr.terminate_member("alice")
    await mgr.shutdown()
