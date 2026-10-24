"""SubAgent 执行器 —— 构造独立 Agent 跑到底（定义式 / Fork 式）。

runner 只依赖 agent/context/permissions/providers，不 import UI（N3）。
多层防线由 build_tool_filter 纯函数表达，可无 UI 单测（N4）。
子 Agent 复用 Agent 类（N6 一致性）：共享 provider/registry/hooks/fs，
独立 permission/context_manager/msgs（F5）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from codepilot.agent import Agent
from codepilot.context.manager import ContextManager
from codepilot.permissions import PermissionManager
from codepilot.providers import BaseProvider
from codepilot.subagents.isolation import (
    build_isolated_runtime,
    role_permission,
    role_provider,
)
from codepilot.subagents.transcript import TranscriptEntry, flatten

if TYPE_CHECKING:
    from codepilot.hooks.engine import HookEngine
    from codepilot.prompt import EnvContext
    from codepilot.protocols import Usage
    from codepilot.subagents.model import AgentRole
    from codepilot.tools.registry import ToolRegistry
    from codepilot.worktree.model import WorktreeHandle

logger = logging.getLogger("codepilot.subagents")


class SubAgentError(Exception):
    """子 Agent 执行失败（模型错误/内部异常）。达轮次上限不属此类（D14）。

    `entries`：失败前已产生的部分轨迹（F11），供任务面板查看。
    """

    def __init__(
        self, message: str, entries: list[TranscriptEntry] | None = None,
    ) -> None:
        super().__init__(message)
        self.entries = entries


@dataclass
class RunResult:
    """一次子 Agent 执行的产出：摘要 + 用量 + 轨迹快照（F11）。"""

    summary: str
    usage: "Usage | None"
    entries: list[TranscriptEntry]


@dataclass
class SharedContext:
    """子 Agent 复用的共享基础设施（F5）与执行默认值。

    runner 只读取，不持有可变状态；每个委派调用从 manager 传入。
    """

    provider: BaseProvider
    registry: "ToolRegistry"
    workspace: Path
    env: "EnvContext"
    shared_hooks: "HookEngine | None" = None
    ctx_config: object | None = None  # context 配置（独立 ContextManager 用）
    parent_permission: PermissionManager | None = None  # 继承模式/human_callback
    max_rounds: int = 18             # 子 Agent 默认最大轮次
    max_depth: int = 1               # 委派深度上限（F7/D9）
    stall_enabled: bool = True
    inherit_plan: bool = False       # 父当前 plan 快照（子 Agent 继承只读）


def build_tool_filter(
    *,
    role: "AgentRole",
    depth: int,
    max_depth: int,
    background: bool,
    inherit_plan: bool,
) -> Callable[["list"], "list"]:
    """多层防线纯函数（F7/D2/D8/D9），输入 Tool 对象列表返回过滤后列表。

    顺序：白名单∪系统 → 去黑名单 → agent 禁（can_delegate ∧ depth<max_depth 才可见）
    → 后台/plan 只读。agent 工具本身 side_effect=False，只读不额外滤它。
    """

    def _filter(tools):
        if role.tools:
            tools = [t for t in tools if t.name in role.tools or t.system_exempt]
        if role.blocked:
            tools = [t for t in tools if t.name not in role.blocked]
        can_agent = role.can_delegate and depth < max_depth
        if not can_agent:
            tools = [t for t in tools if t.name != "agent"]
        if background or inherit_plan:
            tools = [t for t in tools if not t.side_effect]
        # state19: 子 Agent 禁 load_skill（F5 运行时状态隔离）。load_skill 是
        # system_exempt（逃生门），会漏进上面的白名单保留 —— 但会改共享
        # SkillManager 状态，故在防线最末硬排除，不随角色配置恢复。
        tools = [t for t in tools if t.name != "load_skill"]
        return tools

    return _filter


def build_fork_tool_filter(
    parent_names: frozenset[str], *, background: bool
) -> Callable[["list"], "list"]:
    """Fork 式防线：继承父工具集 + 禁 agent + 后台只读（F4/F7）。"""

    def _filter(tools):
        tools = [t for t in tools if t.name in parent_names]
        tools = [t for t in tools if t.name != "agent"]
        if background:
            tools = [t for t in tools if not t.side_effect]
        # state19: Fork 也禁 load_skill（共享 SkillManager 隔离，同 F5）。
        tools = [t for t in tools if t.name != "load_skill"]
        return tools

    return _filter


def _fork_permission(shared: SharedContext) -> PermissionManager | None:
    """Fork 独立权限实例（F5）：无角色，模式继承主当前；无主权限则 None（不判定）。"""
    if shared.parent_permission is None:
        return None
    pm = PermissionManager(
        workspace=shared.workspace,
        mode=shared.parent_permission.mode,
    )
    pm.human_callback = shared.parent_permission.human_callback
    return pm


async def _collect(sub: Agent, msgs: list[dict], *, start: int = 0) -> RunResult:
    """消费子 Agent 循环到结束，收集摘要、末次 usage 与轨迹快照。

    达轮次上限（paused=limit）视为尽力完成（D14），摘要注明；error/cancelled
    抛 `SubAgentError`，并把已产生的部分轨迹挂到异常上（F11）。
    """
    chunks: list[str] = []
    last_usage = None
    limit_reached = False
    error: SubAgentError | None = None
    async for d in sub.run(msgs):
        if d.error:
            error = SubAgentError(f"子 Agent 模型错误: {d.error}")
            break
        if d.cancelled:
            error = SubAgentError("子 Agent 已取消")
            break
        if d.usage is not None:
            last_usage = d.usage
        if d.text:
            chunks.append(d.text)
        if d.paused and d.pause_reason == "limit":
            limit_reached = True
    if error is not None:
        error.entries = flatten(msgs, start=start)
        raise error
    summary = "".join(chunks).strip() or "(无输出)"
    if limit_reached:
        summary += "\n\n[子 Agent 达轮次上限，结论可能不完整]"
    return RunResult(summary=summary, usage=last_usage,
                     entries=flatten(msgs, start=start))


async def run_role_agent(
    role: "AgentRole", prompt: str, *, depth: int, shared: SharedContext,
    worktree: "WorktreeHandle | None" = None,
) -> RunResult:
    """定义式（F3）：空白对话 + 固定角色启动，跑到底返回 RunResult。

    传入 worktree 句柄时使用隔离运行时（独立工具/路径/记忆）；不传时行为与
    state17 完全一致。
    """
    trigger = f"请以角色「{role.name}」执行：{prompt}"
    msgs = [{"role": "user", "content": trigger}]
    if worktree is not None:
        runtime = build_isolated_runtime(shared, role, worktree)
        provider = runtime.provider
        registry = runtime.registry
        workspace = runtime.workspace
        env = runtime.env
        permission = runtime.permission
        ctx_mgr = runtime.context_manager
        skill_injector = runtime.skill_injector
    else:
        provider = role_provider(shared, role.model)
        registry = shared.registry
        workspace = shared.workspace
        env = shared.env
        permission = role_permission(shared, role, workspace=shared.workspace)
        ctx_mgr = ContextManager(provider=provider, config=shared.ctx_config)

        def skill_injector() -> list[str]:
            return [role.body]
    sub = Agent(
        provider=provider,
        registry=registry,
        workspace=workspace,
        env=env,
        permission=permission,
        context_manager=ctx_mgr,
        skill_injector=skill_injector,
        tool_filter=build_tool_filter(
            role=role, depth=depth, max_depth=shared.max_depth,
            background=False, inherit_plan=shared.inherit_plan),
        max_rounds=role.max_rounds or shared.max_rounds,
        hooks=shared.shared_hooks,
        stall_enabled=shared.stall_enabled,
    )
    return await _collect(sub, msgs)


async def run_fork_agent(
    prompt: str, *, parent_msgs: list[dict], parent_names: frozenset[str],
    depth: int, shared: SharedContext,
) -> RunResult:
    """Fork 式（F4）：继承父历史与工具集，强制后台只读，首次请求命中 cache。

    parent_msgs 深拷贝后**裁剪为协议合法前缀**（见 _fork_messages），system 标准构建（D6）。
    轨迹只保留自有部分（触发消息及其后），不再持有继承的父历史（F11）。
    """
    trigger = f"请基于当前上下文执行：{prompt}"
    msgs, own_start = _fork_messages(parent_msgs, trigger)
    provider = shared.provider
    ctx_mgr = ContextManager(provider=provider, config=shared.ctx_config)
    sub = Agent(
        provider=provider,
        registry=shared.registry,
        workspace=shared.workspace,
        env=shared.env,
        permission=_fork_permission(shared),
        context_manager=ctx_mgr,
        tool_filter=build_fork_tool_filter(parent_names, background=True),
        max_rounds=shared.max_rounds,
        hooks=shared.shared_hooks,
        stall_enabled=shared.stall_enabled,
    )
    return await _collect(sub, msgs, start=own_start)


def _has_tool_calls(msg: dict) -> bool:
    """消息是否含未回灌的工具调用（OpenAI tool_calls / Anthropic tool_use 块）。"""
    if msg.get("tool_calls"):
        return True
    content = msg.get("content")
    if isinstance(content, list):
        return any(
            isinstance(b, dict) and b.get("type") == "tool_use" for b in content)
    return False


def _fork_messages(parent_msgs: list[dict], trigger: str) -> tuple[list[dict], int]:
    """构造 Fork 初始消息：深拷贝父历史 + 裁剪 + 追加 trigger。

    Fork 由主 Agent 在「当前轮工具执行中」发起：父 `_msgs` 末尾是
    assistant(tool_calls=agent)，其结果尚未回灌。tool_use 后必须紧跟
    tool_result，否则 Anthropic/OpenAI 都会 400 —— 故裁掉该 dangling 尾消息。
    trigger 并入末条 user（若末条是 user），避免连续同角色。

    返回 `(msgs, own_start)`：`own_start` 为 trigger 所在消息下标，轨迹只取
    该下标之后的自有部分（父子历史不进入任务面板，F11）。
    """
    import copy

    msgs = copy.deepcopy(parent_msgs)
    while msgs and _has_tool_calls(msgs[-1]):
        msgs.pop()
    if msgs and msgs[-1].get("role") == "user":
        content = msgs[-1].get("content")
        if isinstance(content, list):
            msgs[-1]["content"] = [*content, {"type": "text", "text": trigger}]
        else:
            msgs[-1]["content"] = (content or "") + "\n\n" + trigger
    else:
        msgs.append({"role": "user", "content": trigger})
    return msgs, len(msgs) - 1
