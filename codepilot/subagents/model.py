"""SubAgent 数据模型 —— 角色定义 + 后台任务。

角色由 YAML frontmatter（元信息）+ Markdown 正文（子 Agent 系统提示）构成，
本模块只定义结构，解析逻辑在 loader.py（参考 skills/model.py 的职责边界）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from codepilot.protocols import Usage
    from codepilot.subagents.transcript import TranscriptEntry


class RoleError(Exception):
    """角色解析/加载过程的可预期错误（缺字段、非法权限模式、未知工具等）。"""

    pass


@dataclass(frozen=True)
class AgentRole:
    """一个子 Agent 角色的解析结果。"""

    name: str                            # 唯一名（= frontmatter name，命令/委派用）
    description: str                     # 一句话说明（用途）
    body: str                            # 正文 = 子 Agent 系统提示（经 skill_injector 注入）
    tools: frozenset[str] = frozenset()  # 工具白名单（空 = 不约束可选工具）
    blocked: frozenset[str] = frozenset()  # 工具黑名单（额外限制，可含 agent）
    model: str | None = None             # 可选指定模型（None = 继承当前 provider）
    max_rounds: int | None = None        # 可选最大轮次（None = 继承默认）
    permission: str | None = None        # 可选权限模式 manual/auto/strict/permissive（None=继承主）
    can_delegate: bool = False           # 是否放行 agent 工具（F7 显式放行，受深度上限约束）
    path: Path | None = None             # 来源文件（插件/内置/用户/项目）
    ordinal: int = 0                     # 文件内序号（稳定标识）


@dataclass
class SubAgentTask:
    """一个子 Agent 运行任务（前台/后台统一登记）。"""

    task_id: int
    kind: str                            # "defined" | "fork"
    role: str | None                     # fork 为 None
    status: str                          # running / done / failed / cancelled / backgrounded
    prompt: str
    summary: str = ""                    # 完成摘要（回灌用）
    usage: "Usage | None" = None         # 独立 token 用量
    error: str = ""                      # failed 时原因
    created_at: float = 0.0
    finished_at: float = 0.0             # 终态时间（运行中为 0，F11 耗时展示）
    entries: "tuple[TranscriptEntry, ...]" = ()  # 轨迹快照（F11，完成/失败时写入）

    @property
    def duration(self) -> float:
        """任务耗时：终态固定为 finished_at-created_at；运行中持续增长。"""
        if not self.created_at:
            return 0.0
        end = self.finished_at or time.time()
        return max(0.0, end - self.created_at)
