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
from codepilot.permissions.mode import PermissionMode
from codepilot.providers import BaseProvider

if TYPE_CHECKING:
    from codepilot.hooks.engine import HookEngine
    from codepilot.prompt import EnvContext
    from codepilot.protocols import Usage
    from codepilot.subagents.model import AgentRole
    from codepilot.tools.registry import ToolRegistry

logger = logging.getLogger("codepilot.subagents")

# 角色 permission 字符串 → PermissionMode（与 modes.RELEASE_TO_PERMISSION 对齐）
_ROLE_PERMISSION_MODE = {
    "manual": PermissionMode.DEFAULT,
    "auto": PermissionMode.PERMISSIVE,
    "strict": PermissionMode.STRICT,
    "permissive": PermissionMode.PERMISSIVE,
}


class SubAgentError(Exception):
    """子 Agent 执行失败（模型错误/内部异常）。达轮次上限不属此类（D14）。"""

    pass


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
        return tools

    return _filter


def _role_provider(shared: SharedContext, model: str | None) -> BaseProvider:
    """角色 model 覆盖时派生新 provider（继承 base_url/api_key，对齐 isolated skill）。"""
    if model is None or model == shared.provider.config.model:
        return shared.provider
    from dataclasses import replace as _replace

    return BaseProvider.create(_replace(shared.provider.config, model=model))


def _role_permission(shared: SharedContext, role: "AgentRole") -> PermissionManager:
    """独立权限实例：模式取角色指定或继承主；human_callback 透传主 App（D4/D5）。"""
    mode = (_ROLE_PERMISSION_MODE.get(role.permission or "")
            if role.permission
            else (shared.parent_permission.mode if shared.parent_permission else None))
    pm = PermissionManager(
        workspace=shared.workspace,
        mode=mode if mode is not None else PermissionMode.DEFAULT,
    )
    if shared.parent_permission is not None:
        pm.human_callback = shared.parent_permission.human_callback
    return pm


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


async def _collect(sub: Agent, msgs: list[dict]) -> "tuple[str, Usage | None]":
    """消费子 Agent 循环到结束，收集摘要与末次 usage。

    达轮次上限（paused=limit）视为尽力完成（D14），摘要注明；error/cancelled 抛异常。
    """
    chunks: list[str] = []
    last_usage = None
    limit_reached = False
    async for d in sub.run(msgs):
        if d.error:
            raise SubAgentError(f"子 Agent 模型错误: {d.error}")
        if d.cancelled:
            raise SubAgentError("子 Agent 已取消")
        if d.usage is not None:
            last_usage = d.usage
        if d.text:
            chunks.append(d.text)
        if d.paused and d.pause_reason == "limit":
            limit_reached = True
    summary = "".join(chunks).strip() or "(无输出)"
    if limit_reached:
        summary += "\n\n[子 Agent 达轮次上限，结论可能不完整]"
    return summary, last_usage


async def run_role_agent(
    role: "AgentRole", prompt: str, *, depth: int, shared: SharedContext,
) -> "tuple[str, Usage | None]":
    """定义式（F3）：空白对话 + 固定角色启动，跑到底返回 (摘要, 用量)。"""
    trigger = f"请以角色「{role.name}」执行：{prompt}"
    msgs = [{"role": "user", "content": trigger}]
    provider = _role_provider(shared, role.model)
    permission = _role_permission(shared, role)
    ctx_mgr = ContextManager(provider=provider, config=shared.ctx_config)
    sub = Agent(
        provider=provider,
        registry=shared.registry,
        workspace=shared.workspace,
        env=shared.env,
        permission=permission,
        context_manager=ctx_mgr,
        skill_injector=lambda: [role.body],
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
) -> "tuple[str, Usage | None]":
    """Fork 式（F4）：继承父历史与工具集，强制后台只读，首次请求命中 cache。

    parent_msgs 深拷贝后**裁剪为协议合法前缀**（见 _fork_messages），system 标准构建（D6）。
    """
    trigger = f"请基于当前上下文执行：{prompt}"
    msgs = _fork_messages(parent_msgs, trigger)
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
    return await _collect(sub, msgs)


def _has_tool_calls(msg: dict) -> bool:
    """消息是否含未回灌的工具调用（OpenAI tool_calls / Anthropic tool_use 块）。"""
    if msg.get("tool_calls"):
        return True
    content = msg.get("content")
    if isinstance(content, list):
        return any(
            isinstance(b, dict) and b.get("type") == "tool_use" for b in content)
    return False


def _fork_messages(parent_msgs: list[dict], trigger: str) -> list[dict]:
    """构造 Fork 初始消息：深拷贝父历史 + 裁剪 + 追加 trigger。

    Fork 由主 Agent 在「当前轮工具执行中」发起：父 `_msgs` 末尾是
    assistant(tool_calls=agent)，其结果尚未回灌。tool_use 后必须紧跟
    tool_result，否则 Anthropic/OpenAI 都会 400 —— 故裁掉该 dangling 尾消息。
    trigger 并入末条 user（若末条是 user），避免连续同角色。
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
    return msgs
