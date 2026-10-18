"""审批闭环：计划回合、批准/驳回、超时后迟到决定。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from codepilot.protocols import Delta
from codepilot.teams.mailbox import Mailbox
from codepilot.teams.model import (
    TaskStatus,
    TeamMember,
    TeamMemberBackend,
    TeamMemberStatus,
    TeamMeta,
    TeamSettings,
)
from codepilot.teams.runtime import MemberPhase, MemberRuntime
from codepilot.teams.tasks import TaskBoard
from tests.teams.conftest import FakeAgent, FakeManager, wait_until


def _runtime(
    tmp_path: Path, storage, team: str, board: TaskBoard,
    settings: TeamSettings, manager: FakeManager,
    responses,
):
    phase = MemberPhase()
    agent = FakeAgent(responses, phase=phase)
    member = TeamMember(
        name="alice", role="coder", worktree_path=tmp_path / "wt",
        branch="codepilot/team/alpha/alice",
        backend=TeamMemberBackend.INPROCESS,
        requires_approval=True,
        status=TeamMemberStatus.IDLE, created_at=1.0, updated_at=1.0)
    mailbox = Mailbox(storage.mailbox_path(team, "alice"), settings=settings)
    runtime = MemberRuntime(
        member=member,
        team=TeamMeta(
            name="alpha", lead="lead", members=("alice",),
            created_at=1.0, next_member_seq=2),
        agent=agent, mailbox=mailbox, board=board,
        manager=manager, settings=settings, phase=phase)
    return runtime, agent, mailbox


async def _start_and_wait_plan(runtime, manager):
    await runtime.start()
    await wait_until(lambda: any(
        getattr(m.type, "value", m.type) == "plan_request"
        for m in manager.sent))
    assert runtime.status is TeamMemberStatus.BLOCKED


async def test_approved_plan_executes(
    tmp_path: Path, storage, team: str, board: TaskBoard,
    settings: TeamSettings,
):
    manager = FakeManager()
    task = board.create(title="A", assignee="alice")
    runtime, agent, mailbox = _runtime(
        tmp_path, storage, team, board, settings, manager,
        [[Delta(text="计划：先读后写")], [Delta(text="完成")]])
    await _start_and_wait_plan(runtime, manager)
    assert board.get(task.id).status is TaskStatus.PENDING
    assert agent.calls[0]["phase"] == MemberPhase.PLAN

    mailbox.append(mailbox.make(
        sender="lead", recipient="alice", type="plan_decision",
        task_id=task.id, approved=True, reason="ok"))
    runtime.wake()
    await wait_until(
        lambda: board.get(task.id).status is TaskStatus.DONE)
    assert agent.calls[1]["phase"] == MemberPhase.EXECUTE
    assert board.get(task.id).result == "完成"
    await runtime.stop()


async def test_rejected_plan_returns_task_to_pending(
    tmp_path: Path, storage, team: str, board: TaskBoard,
    settings: TeamSettings,
):
    manager = FakeManager()
    task = board.create(title="A", assignee="alice")
    runtime, agent, mailbox = _runtime(
        tmp_path, storage, team, board, settings, manager,
        [[Delta(text="计划")]])
    await _start_and_wait_plan(runtime, manager)

    mailbox.append(mailbox.make(
        sender="lead", recipient="alice", type="plan_decision",
        task_id=task.id, approved=False, reason="太激进"))
    runtime.wake()
    await wait_until(lambda: board.get(task.id).assignee is None)
    assert board.get(task.id).status is TaskStatus.PENDING
    assert len(agent.calls) == 1
    assert runtime.status is TeamMemberStatus.IDLE
    await runtime.stop()


async def test_timeout_blocks_then_late_approval_resumes(
    tmp_path: Path, storage, team: str, board: TaskBoard,
    settings: TeamSettings,
):
    manager = FakeManager()
    fast = replace(
        settings, approval_timeout_seconds=0.05,
        approval_poll_seconds=0.01)
    task = board.create(title="A", assignee="alice")
    runtime, agent, mailbox = _runtime(
        tmp_path, storage, team, board, fast, manager,
        [[Delta(text="计划")], [Delta(text="迟到执行")]])
    await _start_and_wait_plan(runtime, manager)
    await wait_until(lambda: any(
        "超时" in s[3] for s in manager.statuses))
    assert board.get(task.id).status is TaskStatus.PENDING

    mailbox.append(mailbox.make(
        sender="lead", recipient="alice", type="plan_decision",
        task_id=task.id, approved=True, reason="补批"))
    runtime.wake()
    await wait_until(
        lambda: board.get(task.id).status is TaskStatus.DONE)
    assert len(agent.calls) == 2
    await runtime.stop()


async def test_decision_for_other_task_is_not_consumed(
    tmp_path: Path, storage, team: str, board: TaskBoard,
    settings: TeamSettings,
):
    manager = FakeManager()
    task = board.create(title="A", assignee="alice")
    runtime, _, mailbox = _runtime(
        tmp_path, storage, team, board, settings, manager,
        [[Delta(text="计划")], [Delta(text="执行")]])
    await _start_and_wait_plan(runtime, manager)

    other = mailbox.append(mailbox.make(
        sender="lead", recipient="alice", type="plan_decision",
        task_id=999, approved=True, reason="other"))
    runtime.wake()
    await wait_until(lambda: runtime.status is TeamMemberStatus.BLOCKED)
    remaining = {m.id: m.read for m in mailbox.peek()}
    assert remaining[other.id] is False

    mailbox.append(mailbox.make(
        sender="lead", recipient="alice", type="plan_decision",
        task_id=task.id, approved=True, reason="ok"))
    runtime.wake()
    await wait_until(
        lambda: board.get(task.id).status is TaskStatus.DONE)
    await runtime.stop()


async def test_stop_during_approval(
    tmp_path: Path, storage, team: str, board: TaskBoard,
    settings: TeamSettings,
):
    manager = FakeManager()
    board.create(title="A", assignee="alice")
    runtime, _, _ = _runtime(
        tmp_path, storage, team, board, settings, manager,
        [[Delta(text="计划")]])
    await _start_and_wait_plan(runtime, manager)
    await runtime.stop()
    assert not runtime.running
