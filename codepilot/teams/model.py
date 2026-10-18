"""state19 团队领域模型：枚举、数据类与错误类型。

本模块只依赖标准库，不做 IO、不依赖 Agent/UI。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class TeamError(Exception):
    """团队系统错误基类。"""


class TeamSettingsError(TeamError):
    """team: 配置非法。"""


class TeamNameError(TeamError):
    """小组名/成员名非法。"""


class TeamLockError(TeamError):
    """锁获取失败或超时。"""


class TeamTaskError(TeamError):
    """任务板操作失败。"""


class TeamMessageError(TeamError):
    """消息校验或投递失败。"""


class TeamMemberError(TeamError):
    """成员生命周期操作失败。"""


class TeamBackendError(TeamError):
    """成员运行后端不可用或未实现。"""


class TeamMemberStatus(str, Enum):
    SPAWNING = "spawning"
    RUNNING = "running"
    IDLE = "idle"
    BLOCKED = "blocked"
    OFFLINE = "offline"
    TERMINATED = "terminated"
    FAILED = "failed"


class TeamMemberBackend(str, Enum):
    INPROCESS = "inprocess"
    TERMINAL = "terminal"      # state20 预留，本阶段不实现


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    CANCELLED = "cancelled"


class TeamMessageType(str, Enum):
    TEXT = "text"
    BROADCAST = "broadcast"
    PLAN_REQUEST = "plan_request"
    PLAN_DECISION = "plan_decision"
    STATUS = "status"


@dataclass(frozen=True)
class TeamSettings:
    """团队系统配置。

    `root` 为用户目录下的小组根；其余字段均可由 config.yaml 的 team: 段覆盖。
    """

    root: Path
    approval_timeout_seconds: float = 600.0
    approval_poll_seconds: float = 0.5
    lock_timeout_seconds: float = 5.0
    lock_stale_seconds: float = 30.0
    lock_retry_seconds: float = 0.05
    message_summary_chars: int = 120
    max_unread_per_wakeup: int = 20
    default_backend: str = "auto"


@dataclass(frozen=True)
class TeamMeta:
    name: str
    lead: str
    members: tuple[str, ...]
    created_at: float
    next_member_seq: int


@dataclass(frozen=True)
class TeamMember:
    name: str
    role: str
    worktree_path: Path
    branch: str
    backend: TeamMemberBackend
    requires_approval: bool
    status: TeamMemberStatus
    created_at: float
    updated_at: float
    note: str = ""


@dataclass(frozen=True)
class MemberRef:
    """名称注册表中的成员视图（投递第一段使用）。"""

    name: str
    role: str
    status: TeamMemberStatus
    backend: TeamMemberBackend
    mailbox_path: Path
    worktree_path: Path
    branch: str
    requires_approval: bool


@dataclass(frozen=True)
class Task:
    id: int
    title: str
    description: str
    status: TaskStatus
    assignee: str | None
    depends_on: tuple[int, ...]
    created_at: float
    updated_at: float
    result: str = ""


@dataclass(frozen=True)
class TeamMessage:
    id: str
    sender: str
    recipient: str
    type: TeamMessageType
    body: str
    summary: str
    timestamp: float
    read: bool
    task_id: int | None = None
    approved: bool | None = None
    reason: str = ""
    status: str = ""


@dataclass(frozen=True)
class TeamEvent:
    """推送给 Lead 界面的轻量事件；正文真源仍在邮箱。"""

    kind: str
    member: str
    task_id: int | None
    summary: str
    mailbox_path: Path | None = None
