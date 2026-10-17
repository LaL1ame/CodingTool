"""隔离子 Agent 的运行时装配。

把 Worktree 句柄翻译成一套独立运行环境：
- 文件/命令工具重新绑定 worktree（系统工具共享同一实例）；
- env / permission / context offload 全部以 worktree 为根；
- 项目指令与记忆按 worktree 绝对路径重建；
- 注入一段隔离路径说明，降低模型引用主仓路径的概率。

不 import UI；runner 通过本模块构造隔离 Agent。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from codepilot.context.manager import ContextManager
from codepilot.memory.inject import MemoryInjector
from codepilot.memory.store import NoteStore
from codepilot.permissions import PermissionManager
from codepilot.permissions.mode import PermissionMode
from codepilot.prompt import EnvContext
from codepilot.providers import BaseProvider
from codepilot.tools import (
    EditTool,
    GlobTool,
    GrepTool,
    ReadTool,
    RunTool,
    WriteTool,
)
from codepilot.tools.registry import ToolRegistry
from codepilot.worktree.model import WorktreeHandle

if TYPE_CHECKING:
    from codepilot.subagents.model import AgentRole
    from codepilot.subagents.runner import SharedContext

# 角色 permission 字符串 → PermissionMode（与 modes.RELEASE_TO_PERMISSION 对齐）
_ROLE_PERMISSION_MODE = {
    "manual": PermissionMode.DEFAULT,
    "auto": PermissionMode.PERMISSIVE,
    "strict": PermissionMode.STRICT,
    "permissive": PermissionMode.PERMISSIVE,
}

# 需要按工作区重新实例化的工具；其余（agent/load_skill/MCP）共享。
_BOUND_TOOL_NAMES = frozenset({"read", "write", "edit", "glob", "grep", "run"})


@dataclass
class IsolatedRuntime:
    """一次隔离运行所需的全部路径相关组件。"""

    provider: BaseProvider
    registry: ToolRegistry
    workspace: Path
    env: EnvContext
    permission: PermissionManager
    context_manager: ContextManager
    skill_injector: Callable[[], list[str]]


def role_provider(shared: "SharedContext", model: str | None) -> BaseProvider:
    """角色 model 覆盖时派生新 provider（继承 base_url/api_key）。"""
    if model is None or model == shared.provider.config.model:
        return shared.provider
    return BaseProvider.create(replace(shared.provider.config, model=model))


def role_permission_mode(
    shared: "SharedContext", role: "AgentRole",
) -> PermissionMode:
    if role.permission:
        return _ROLE_PERMISSION_MODE.get(role.permission, PermissionMode.DEFAULT)
    if shared.parent_permission is not None:
        return shared.parent_permission.mode
    return PermissionMode.DEFAULT


def role_permission(
    shared: "SharedContext", role: "AgentRole", *,
    workspace: Path | None = None,
) -> PermissionManager:
    """独立权限实例；human_callback 从父权限继承。"""
    pm = PermissionManager(
        workspace=workspace or shared.workspace,
        mode=role_permission_mode(shared, role),
    )
    if shared.parent_permission is not None:
        pm.human_callback = shared.parent_permission.human_callback
    return pm


def build_isolated_registry(
    template: ToolRegistry, workspace: Path,
) -> ToolRegistry:
    """文件/命令工具绑定 workspace；系统工具共享模板中的同一实例。"""
    registry = ToolRegistry()
    registry.register(ReadTool(workspace))
    registry.register(WriteTool(workspace))
    registry.register(EditTool(workspace))
    registry.register(GlobTool(workspace))
    registry.register(GrepTool(workspace))
    registry.register(RunTool(workspace))
    for tool in template.list_all():
        if tool.name not in _BOUND_TOOL_NAMES:
            registry.register(tool)
    return registry


def isolation_prompt_block(handle: WorktreeHandle) -> str:
    return (
        "【隔离工作目录】\n"
        f"你的工作目录是：{handle.path}\n"
        f"分支：{handle.branch}\n"
        "所有文件读写与命令都限定在此目录；不要引用或修改主工作目录。\n"
        "需要交接的改动请留在本目录，并在结论中说明。"
    )


def build_isolated_runtime(
    shared: "SharedContext", role: "AgentRole", handle: WorktreeHandle,
) -> IsolatedRuntime:
    provider = role_provider(shared, role.model)
    workspace = handle.path
    registry = build_isolated_registry(shared.registry, workspace)
    permission = role_permission(shared, role, workspace=workspace)
    context_manager = ContextManager(
        provider=provider,
        config=shared.ctx_config,
        offload_dir=workspace / ".codepilot" / "context",
    )
    memory = MemoryInjector(workspace, store=NoteStore(workspace))
    block = isolation_prompt_block(handle)

    def _injector() -> list[str]:
        blocks = [block]
        if role.body:
            blocks.append(role.body)
        blocks.extend(memory.build())
        return blocks

    return IsolatedRuntime(
        provider=provider,
        registry=registry,
        workspace=workspace,
        env=EnvContext(cwd=str(workspace), date=shared.env.date),
        permission=permission,
        context_manager=context_manager,
        skill_injector=_injector,
    )
