"""Lead 编排工具：13 个工具的成功/失败路径。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from codepilot.protocols import Delta
from codepilot.subagents.model import AgentRole
from codepilot.teams.manager import TeamManager
from codepilot.teams.model import (
    TeamMemberBackend,
    TeamMemberStatus,
    TeamSettings,
)
from codepilot.teams.tools_lead import LEAD_TOOL_NAMES, build_lead_tools
from codepilot.tools.registry import ToolRegistry
from codepilot.worktree.model import WorktreeHandle
from tests.teams.conftest import FakeAgent


class FakeWorktree:
    def __init__(self, root: Path):
        self.root = root
        self.released: list[str] = []

    def enter(self, name, *, role, task_id):
        path = self.root / name.replace("/", "__")
        path.mkdir(parents=True, exist_ok=True)
        return WorktreeHandle(
            name=name, path=path, branch=f"codepilot/{name}",
            base="base", created_at=1.0)

    def release(self, handle):
        self.released.append(handle.name)

    def leave(self, handle):
        pass


def _manager(settings: TeamSettings, tmp_path: Path) -> TeamManager:
    workspace = tmp_path / "main"
    workspace.mkdir(exist_ok=True)

    def factory(**kw):
        return FakeAgent([[Delta(text="ok")]])

    return TeamManager(
        settings, workspace=workspace,
        base_registry=ToolRegistry(),
        roles={"coder": AgentRole(
            name="coder", description="coder", body="", tools=frozenset())},
        provider=SimpleNamespace(config=SimpleNamespace(model="m")),
        worktree=FakeWorktree(tmp_path / "wt"),
        agent_factory=factory)


def _member(tmp_path: Path, name: str = "alice"):
    from codepilot.teams.model import TeamMember

    return TeamMember(
        name=name, role="coder", worktree_path=tmp_path / f"wt-{name}",
        branch=f"codepilot/team/alpha/{name}",
        backend=TeamMemberBackend.INPROCESS,
        requires_approval=False, status=TeamMemberStatus.IDLE,
        created_at=1.0, updated_at=1.0)


@pytest.fixture
def setup(settings: TeamSettings, tmp_path: Path):
    mgr = _manager(settings, tmp_path)
    tools = {t.name: t for t in build_lead_tools(mgr)}
    return mgr, tools, tmp_path


def test_tool_set(setup):
    _, tools, _ = setup
    assert set(tools) == LEAD_TOOL_NAMES


async def test_team_create_list_use_show(setup):
    mgr, tools, _ = setup
    created = await tools["team_create"].execute(name="alpha")
    assert "alpha" in created.output
    listed = await tools["team_list"].execute()
    assert "* alpha" in listed.output
    shown = await tools["team_show"].execute()
    assert "任务" in shown.output

    await tools["team_create"].execute(name="beta")
    used = await tools["team_use"].execute(name="alpha")
    assert "alpha" in used.output
    wrong = await tools["team_show"].execute(name="beta")
    assert wrong.error


async def test_task_tools(setup):
    mgr, tools, tmp_path = setup
    await tools["team_create"].execute(name="alpha")
    mgr.registry().add_member(_member(tmp_path))
    a = await tools["team_task_create"].execute(title="A")
    assert "#1" in a.output
    b = await tools["team_task_create"].execute(
        title="B", depends_on=[1], assignee="alice")
    assert "assignee=alice" in b.output
    updated = await tools["team_task_update"].execute(
        task_id=1, status="in_progress")
    assert "in_progress" in updated.output
    listed = await tools["team_task_list"].execute()
    assert "A" in listed.output and "B" in listed.output


async def test_send_broadcast_inbox(setup):
    mgr, tools, tmp_path = setup
    await tools["team_create"].execute(name="alpha")
    mgr.registry().add_member(_member(tmp_path))
    sent = await tools["team_send"].execute(to="alice", body="hi")
    assert "alice" in sent.output
    broadcast = await tools["team_broadcast"].execute(body="all")
    assert "送达 1" in broadcast.output

    mgr.send(sender="alice", recipient="lead", type="text", body="reply")
    inbox = await tools["team_inbox"].execute(limit=10)
    assert "reply" in inbox.output
    empty = await tools["team_inbox"].execute(limit=10)
    assert "没有未读" in empty.output


async def test_decision(setup):
    mgr, tools, tmp_path = setup
    await tools["team_create"].execute(name="alpha")
    mgr.registry().add_member(_member(tmp_path))
    task = mgr.create_task(title="A", assignee="alice")
    mgr.send(
        sender="alice", recipient="lead", type="plan_request",
        body="plan", task_id=task.id)
    decided = await tools["team_decide"].execute(
        task_id=task.id, approved=True, reason="ok")
    assert "批准" in decided.output
    missing = await tools["team_decide"].execute(
        task_id=999, approved=True)
    assert missing.error


async def test_spawn_and_terminate(setup):
    mgr, tools, _ = setup
    await tools["team_create"].execute(name="alpha")
    spawned = await tools["team_spawn"].execute(
        name="alice", role="coder")
    assert "alice" in spawned.output
    assert mgr.registry().resolve("alice").status is TeamMemberStatus.IDLE
    terminated = await tools["team_terminate"].execute(name="alice")
    assert "已终止" in terminated.output
    assert mgr.registry().resolve("alice").status is (
        TeamMemberStatus.TERMINATED)
    await mgr.shutdown()


async def test_no_team_errors(setup):
    _, tools, _ = setup
    result = await tools["team_task_create"].execute(title="A")
    assert result.error
    inbox = await tools["team_inbox"].execute()
    assert inbox.error
