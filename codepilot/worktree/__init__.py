"""Worktree 工作目录隔离领域层。

只关心 Git worktree 与文件系统：命名校验、配置、Git 封装、元数据、
环境初始化、生命周期编排与过期清理。不 import Agent / UI。
"""

from codepilot.worktree.initializer import WorktreeInitError
from codepilot.worktree.manager import WorktreeError, WorktreeManager
from codepilot.worktree.naming import WorktreeNameError
from codepilot.worktree.settings import (
    WorktreeSettings,
    WorktreeSettingsError,
    load_settings,
)

__all__ = [
    "WorktreeError",
    "WorktreeInitError",
    "WorktreeManager",
    "WorktreeNameError",
    "WorktreeSettings",
    "WorktreeSettingsError",
    "load_settings",
]
