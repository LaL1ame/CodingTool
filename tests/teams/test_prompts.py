"""团队提示块内容。"""

from pathlib import Path

from codepilot.teams.model import (
    MemberRef,
    Task,
    TaskStatus,
    TeamMember,
    TeamMemberBackend,
    TeamMemberStatus,
    TeamMessage,
    TeamMessageType,
    TeamMeta,
)
from codepilot.teams.prompts import (
    lead_context_block,
    member_context_block,
    message_turn_prompt,
    task_execution_prompt,
    task_planning_prompt,
)


def _team() -> TeamMeta:
    return TeamMeta(
        name="alpha", lead="lead", members=("bob",),
        created_at=1.0, next_member_seq=2)


def _member(tmp_path: Path, **kw) -> TeamMember:
    return TeamMember(
        name="bob", role="coder", worktree_path=tmp_path / "wt",
        branch="codepilot/team/alpha/bob",
        backend=TeamMemberBackend.INPROCESS,
        requires_approval=kw.get("requires_approval", False),
        status=TeamMemberStatus.IDLE, created_at=1.0, updated_at=1.0)


def _ref(tmp_path: Path) -> MemberRef:
    return MemberRef(
        name="bob", role="coder", status=TeamMemberStatus.IDLE,
        backend=TeamMemberBackend.INPROCESS,
        mailbox_path=tmp_path / "bob.jsonl",
        worktree_path=tmp_path / "wt",
        branch="codepilot/team/alpha/bob", requires_approval=False)


def _task() -> Task:
    return Task(
        id=3, title="写文档", description="desc", status=TaskStatus.PENDING,
        assignee="bob", depends_on=(1, 2), created_at=1.0, updated_at=1.0)


def test_lead_without_team():
    text = lead_context_block(None, [], {})
    assert "team_create" in text
    assert "task_*" in text


def test_lead_with_team(tmp_path: Path):
    text = lead_context_block(
        _team(), [_ref(tmp_path)],
        {"pending": 1, "in_progress": 0, "done": 2, "cancelled": 0})
    assert "alpha" in text and "bob" in text
    assert "pending=1" in text and "done=2" in text
    assert "team_inbox" in text


def test_member_context(tmp_path: Path):
    text = member_context_block(
        _team(), _member(tmp_path), [_ref(tmp_path)])
    assert "alpha" in text and "bob" in text
    assert str(tmp_path / "wt") in text
    assert "任务边界" in text
    assert "免审批" in text


def test_member_context_requires_approval(tmp_path: Path):
    text = member_context_block(
        _team(), _member(tmp_path, requires_approval=True), [])
    assert "必须先提交计划" in text


def test_task_prompts_include_deps_and_guard():
    plan = task_planning_prompt(_task())
    assert "#3" in plan and "#1" in plan
    assert "不得写文件" in plan

    exec_prompt = task_execution_prompt(
        _task(), unread=[], approved_plan="1. 写")
    assert "已批准的计划" in exec_prompt
    assert "标记 done" in exec_prompt


def test_execution_prompt_includes_messages():
    msg = TeamMessage(
        id="m", sender="lead", recipient="bob", type=TeamMessageType.TEXT,
        body="请先看规范", summary="请先看规范", timestamp=1.0, read=False)
    text = task_execution_prompt(_task(), unread=[msg])
    assert "请先看规范" in text and "来自 lead" in text
    assert "任务边界收到的消息" in text


def test_message_turn_prompt():
    msg = TeamMessage(
        id="m", sender="lead", recipient="bob", type=TeamMessageType.TEXT,
        body="hello", summary="hello", timestamp=1.0, read=False)
    text = message_turn_prompt([msg])
    assert "hello" in text
    assert "保持空闲" in text
