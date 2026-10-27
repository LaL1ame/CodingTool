"""A4 coordinator 专注模式：双锁检测 + Lead 工具面收窄 + 提示块。

双锁：config.team.coordinator_enabled ∧ 环境变量 CODEPILOT_COORDINATOR 都开才生效。
生效时主 Agent(Lead) 移除写文件工具（write/edit），保留 read/run/team_*；
成员与普通子 Agent 不受影响。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

_COORDINATOR_ENV = "CODEPILOT_COORDINATOR"
_TRUTHY = {"1", "true", "on", "yes"}

# 写文件类工具：coordinator 下从 Lead 工具面移除。
_WRITE_TOOLS = frozenset({"write", "edit"})


@dataclass(frozen=True)
class CoordinatorState:
    enabled: bool
    source: str                        # "config+env" | "config" | "env" | ""
    write_tools_removed: tuple[str, ...] = ()


def coordinator_enabled(
    settings, env: Mapping[str, str] | None = None,
) -> CoordinatorState:
    """两把锁都开才生效：配置能力开关 ∧ 环境变量。"""
    cfg_on = bool(getattr(settings, "coordinator_enabled", False))
    env_val = (env if env is not None else os.environ).get(_COORDINATOR_ENV, "")
    env_on = env_val.strip().lower() in _TRUTHY
    if cfg_on and env_on:
        return CoordinatorState(
            enabled=True, source="config+env",
            write_tools_removed=_WRITE_TOOLS)
    if cfg_on:
        return CoordinatorState(enabled=False, source="config")
    if env_on:
        return CoordinatorState(enabled=False, source="env")
    return CoordinatorState(enabled=False, source="")


def coordinator_tool_filter(tools, state: CoordinatorState):
    """coordinator 生效时从 Lead 工具面移除写文件工具（返回新列表，不改原对象）。"""
    if not state.enabled:
        return list(tools)
    return [t for t in tools if t.name not in _WRITE_TOOLS]


def coordinator_context_block(state: CoordinatorState) -> str:
    """Lead volatile 块：coordinator 纯编排者提示。"""
    if not state.enabled:
        return ""
    return (
        "## coordinator 专注模式（已开启）\n"
        "你是纯编排者：文件修改全部交给成员执行，你**不直接写文件**。\n"
        "你的职责：派生/终止成员、审计划/决定、收发消息、建任务、用 team_merge 合并成果。\n"
        "写文件工具已从你的工具面移除；如需跑 git 合并等命令可用 run。")