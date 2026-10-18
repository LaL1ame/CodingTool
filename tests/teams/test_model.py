"""模型枚举、错误层级与 dataclass 不可变。"""

from pathlib import Path

import pytest

from codepilot.teams.model import (
    MemberRef,
    Task,
    TaskStatus,
    TeamBackendError,
    TeamError,
    TeamMemberStatus,
    TeamMessage,
    TeamMessageType,
    TeamSettings,
    TeamSettingsError,
    TeamTaskError,
)


def test_status_values():
    assert TeamMemberStatus.BLOCKED.value == "blocked"
    assert TeamMessageType.PLAN_REQUEST.value == "plan_request"
    assert TaskStatus.IN_PROGRESS.value == "in_progress"


def test_error_hierarchy():
    assert issubclass(TeamSettingsError, TeamError)
    assert issubclass(TeamTaskError, TeamError)
    assert issubclass(TeamBackendError, TeamError)


def test_dataclasses_are_frozen():
    task = Task(
        id=1, title="t", description="", status=TaskStatus.PENDING,
        assignee=None, depends_on=(), created_at=0.0, updated_at=0.0)
    with pytest.raises(Exception):
        task.title = "x"  # type: ignore[misc]


def test_settings_defaults(tmp_path: Path):
    s = TeamSettings(root=tmp_path)
    assert s.approval_timeout_seconds == 600.0
    assert s.default_backend == "auto"


def test_message_defaults():
    msg = TeamMessage(
        id="1", sender="lead", recipient="bob", type=TeamMessageType.TEXT,
        body="hi", summary="hi", timestamp=1.0, read=False)
    assert msg.task_id is None
    assert msg.approved is None


def test_member_ref_fields(tmp_path: Path):
    ref = MemberRef(
        name="bob", role="coder", status=TeamMemberStatus.IDLE,
        backend="inprocess",  # 允许字符串，运行时按需 coerce
        mailbox_path=tmp_path / "b.jsonl", worktree_path=tmp_path,
        branch="codepilot/team/alpha/bob", requires_approval=True)
    assert ref.requires_approval
