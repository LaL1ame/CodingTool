"""成员运行时：idle/唤醒、任务循环、失败/暂停、上下文连续与工具过滤。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

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
from codepilot.teams.runtime import (
    MemberPhase,
    MemberRuntime,
    build_member_tool_filter,
)
from codepilot.teams.tasks import TaskBoard
from tests.teams.conftest import FakeAgent, FakeManager, wait_until


def _team() -> TeamMeta:
    return TeamMeta(
        name="alpha", lead="lead", members=("alice",),
        created_at=1.0, next_member_seq=2)


def _member(tmp_path: Path, *, approval: bool = False) -> TeamMember:
    return TeamMember(
        name="alice", role="coder", worktree_path=tmp_path / "wt",
        branch="codepilot/team/alpha/alice",
        backend=TeamMemberBackend.INPROCESS,
        requires_approval=approval,
        status=TeamMemberStatus.IDLE, created_at=1.0, updated_at=1.0)


def _mailbox(storage, team: str, settings: TeamSettings) -> Mailbox:
    return Mailbox(storage.mailbox_path(team, "alice"), settings=settings)


def _runtime(
    tmp_path: Path, storage, team: str, board: TaskBoard,
    settings: TeamSettings, manager: FakeManager,
    responses=None, *, approval: bool = False,
):
    phase = MemberPhase()
    agent = FakeAgent(responses, phase=phase)
    runtime = MemberRuntime(
        member=_member(tmp_path, approval=approval),
        team=_team(), agent=agent,
        mailbox=_mailbox(storage, team, settings),
        board=board, manager=manager, settings=settings, phase=phase)
    return runtime, agent


async def test_start_idle_and_stop(
    tmp_path: Path, storage, team: str, board: TaskBoard,
    settings: TeamSettings,
):
    manager = FakeManager()
    runtime, _ = _runtime(tmp_path, storage, team, board, settings, manager)
    cwd = Path.cwd()
    await runtime.start()
    await wait_until(lambda: runtime.status is TeamMemberStatus.IDLE)
    assert runtime.running
    await runtime.stop()
    assert not runtime.running
    assert Path.cwd() == cwd


async def test_execute_assigned_task(
    tmp_path: Path, storage, team: str, board: TaskBoard,
    settings: TeamSettings,
):
    manager = FakeManager()
    task = board.create(title="A", assignee="alice")
    runtime, agent = _runtime(
        tmp_path, storage, team, board, settings, manager,
        [[Delta(text="done")]])
    await runtime.start()
    await wait_until(
        lambda: board.get(task.id).status is TaskStatus.DONE)
    assert board.get(task.id).result == "done"
    assert agent.calls
    assert TeamMemberStatus.RUNNING in manager.status_values()
    assert runtime.status is TeamMemberStatus.IDLE
    await runtime.stop()


async def test_paused_task_stays_in_progress(
    tmp_path: Path, storage, team: str, board: TaskBoard,
    settings: TeamSettings,
):
    manager = FakeManager()
    task = board.create(title="A", assignee="alice")
    runtime, _ = _runtime(
        tmp_path, storage, team, board, settings, manager,
        [[Delta(paused=True, pause_reason="limit")]])
    await runtime.start()
    await wait_until(lambda: runtime.status is TeamMemberStatus.BLOCKED)
    assert board.get(task.id).status is TaskStatus.IN_PROGRESS
    await runtime.stop()


async def test_error_marks_member_failed(
    tmp_path: Path, storage, team: str, board: TaskBoard,
    settings: TeamSettings,
):
    manager = FakeManager()
    task = board.create(title="A", assignee="alice")
    runtime, _ = _runtime(
        tmp_path, storage, team, board, settings, manager,
        [[Delta(error="boom")]])
    await runtime.start()
    await wait_until(lambda: runtime.status is TeamMemberStatus.FAILED)
    assert board.get(task.id).status is TaskStatus.IN_PROGRESS
    await runtime.stop()


async def test_message_turn_consumes_at_boundary(
    tmp_path: Path, storage, team: str, board: TaskBoard,
    settings: TeamSettings,
):
    manager = FakeManager()
    box = _mailbox(storage, team, settings)
    box.append(box.make(
        sender="lead", recipient="alice", type="text", body="hello alice"))
    runtime, agent = _runtime(
        tmp_path, storage, team, board, settings, manager,
        [[Delta(text="收到")]])
    await runtime.start()
    await wait_until(lambda: bool(agent.calls))
    await wait_until(lambda: runtime.status is TeamMemberStatus.IDLE)
    assert "hello alice" in agent.calls[0]["last"]["content"]
    await runtime.stop()


async def test_context_continues_across_tasks(
    tmp_path: Path, storage, team: str, board: TaskBoard,
    settings: TeamSettings,
):
    manager = FakeManager()
    a = board.create(title="A", assignee="alice")
    b = board.create(title="B", assignee="alice")
    runtime, agent = _runtime(
        tmp_path, storage, team, board, settings, manager,
        [[Delta(text="one")], [Delta(text="two")]])
    await runtime.start()
    await wait_until(
        lambda: board.get(a.id).status is TaskStatus.DONE
        and board.get(b.id).status is TaskStatus.DONE)
    assert len(agent.calls) >= 2
    assert agent.calls[1]["len"] > agent.calls[0]["len"]
    await runtime.stop()


class _Tool:
    def __init__(self, name, *, side_effect=False, system_exempt=False):
        self.name = name
        self.side_effect = side_effect
        self.system_exempt = system_exempt


def test_member_tool_filter_plan_and_execute():
    phase = MemberPhase()
    role = SimpleNamespace(tools=["read"], blocked={"blocked_tool"})
    flt = build_member_tool_filter(role, phase)
    tools = [
        _Tool("read"), _Tool("write", side_effect=True),
        _Tool("task_list"), _Tool("task_update", side_effect=True),
        _Tool("blocked_tool"), _Tool("agent", side_effect=True),
        _Tool("load_skill", system_exempt=True),
    ]
    phase.value = MemberPhase.PLAN
    planned = {t.name for t in flt(tools)}
    assert planned == {"read", "task_list", "load_skill"}

    phase.value = MemberPhase.EXECUTE
    executed = {t.name for t in flt(tools)}
    assert executed == {"read", "task_list", "task_update", "load_skill"}
    assert "agent" not in executed
    assert "blocked_tool" not in executed
