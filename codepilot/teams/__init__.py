"""state19 团队系统（Team Lead）。

领域层导出；Lead/成员工具与 TeamManager 在对应模块中定义，避免循环 import。
"""

from codepilot.teams.model import (
    MemberRef,
    Task,
    TaskStatus,
    TeamBackendError,
    TeamError,
    TeamEvent,
    TeamLockError,
    TeamMember,
    TeamMemberBackend,
    TeamMemberError,
    TeamMemberStatus,
    TeamMessage,
    TeamMessageError,
    TeamMessageType,
    TeamMeta,
    TeamNameError,
    TeamSettings,
    TeamSettingsError,
    TeamTaskError,
)
from codepilot.teams.settings import load_team_settings, select_backend
from codepilot.teams.tools_lead import LEAD_TOOL_NAMES
from codepilot.teams.tools_member import MEMBER_TOOL_NAMES

ALL_TEAM_TOOL_NAMES = LEAD_TOOL_NAMES | MEMBER_TOOL_NAMES


def filter_team_tools(names) -> frozenset[str]:
    """从工具名集合中去掉所有团队工具（Fork 父工具集快照用）。"""
    return frozenset(n for n in names if n not in ALL_TEAM_TOOL_NAMES)


__all__ = [
    "MemberRef",
    "Task",
    "TaskStatus",
    "TeamBackendError",
    "TeamError",
    "TeamEvent",
    "TeamLockError",
    "TeamMember",
    "TeamMemberBackend",
    "TeamMemberError",
    "TeamMemberStatus",
    "TeamMessage",
    "TeamMessageError",
    "TeamMessageType",
    "TeamMeta",
    "TeamNameError",
    "TeamSettings",
    "TeamSettingsError",
    "TeamTaskError",
    "ALL_TEAM_TOOL_NAMES",
    "LEAD_TOOL_NAMES",
    "MEMBER_TOOL_NAMES",
    "filter_team_tools",
    "load_team_settings",
    "select_backend",
]
