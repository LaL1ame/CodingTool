"""SubAgentManager —— 委派入口 + 后台任务注册表 + 深度/并发上限 + 状态追踪。

对齐 spec F8/F10：
- 委派分流 defined/fork；三种进后台（显式 background、前台 timeout 转后台、/bg 手动切）。
- 深度上限（contextvar）与后台并发上限。
- 故障隔离：子 Agent 失败只更新 task 状态 + on_task_done 回调，绝不外抛（N2）。
- 不 import UI（N3）：notify / on_task_done 均为注入回调。
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from codepilot.protocols import ToolResult
from codepilot.subagents.model import AgentRole, RoleError, SubAgentTask
from codepilot.subagents.runner import (
    SharedContext,
    SubAgentError,
    run_fork_agent,
    run_role_agent,
)

if TYPE_CHECKING:
    from codepilot.tools.registry import ToolRegistry

logger = logging.getLogger("codepilot.subagents")

# 委派深度（contextvar：主 Agent 深度 0，子 Agent +1，孙 +2…）。后台任务
# create_task 会浅拷贝当前值，深度随委派链正确传播。
_depth_ctx: contextvars.ContextVar[int] = contextvars.ContextVar(
    "codepilot_subagent_depth", default=0)


class SubAgentManager:
    def __init__(
        self,
        known: dict[str, AgentRole],
        *,
        workspace,
        provider,
        registry: "ToolRegistry",
        env,
        ctx_config=None,
        shared_hooks=None,
        parent_permission=None,
        max_concurrent: int = 4,
        max_depth: int = 1,
        max_rounds: int = 18,
        stall_enabled: bool = True,
        parent_visible: Callable[[], set[str]] | None = None,
        parent_msgs: Callable[[], list[dict]] | None = None,
        notify: Callable[[str], None] | None = None,
        on_task_done: Callable[[SubAgentTask], None] | None = None,
    ) -> None:
        self._known = known
        self._max_concurrent = max_concurrent
        self._max_depth = max_depth
        self._next_id = 1
        self._tasks: dict[int, SubAgentTask] = {}
        self._running: set[int] = set()
        # 前台等待表：task_id → (子循环 task, 转后台 marker)。/bg 设 marker 解除 await。
        self._foreground: dict[int, tuple[asyncio.Task, asyncio.Event]] = {}
        self._notify = notify
        self._on_task_done = on_task_done
        self._parent_visible = parent_visible or (
            lambda: {t.name for t in registry.list_all()})
        self._parent_msgs = parent_msgs or (lambda: [])
        self._shared = SharedContext(
            provider=provider,
            registry=registry,
            workspace=workspace,
            env=env,
            shared_hooks=shared_hooks,
            ctx_config=ctx_config,
            parent_permission=parent_permission,
            max_rounds=max_rounds,
            max_depth=max_depth,
            stall_enabled=stall_enabled,
        )

    # —— 查询 ——
    @property
    def known(self) -> dict[str, AgentRole]:
        return self._known

    def tasks(self) -> list[SubAgentTask]:
        return list(self._tasks.values())

    def counts(self) -> tuple[int, int]:
        """(运行中数量, 已完成数量) —— TUI 提示行用；只读快照，不引 UI。"""
        tasks = self._tasks.values()
        running = sum(
            1 for t in tasks if t.status in ("running", "backgrounded"))
        done = sum(1 for t in tasks if t.status == "done")
        return running, done

    def role_list_block(self) -> str:
        """可委派角色名册（state17 修正：注入 system prompt，让模型知道有哪些角色）。

        模型看不到角色文件，若不注入名册便不会主动用 agent 工具（真机验收暴露）。
        """
        if not self._known:
            return ""
        lines = ["## 可委派的子 Agent 角色（用 agent 工具，mode=defined，role=名字）"]
        for role in sorted(self._known.values(), key=lambda r: r.name):
            lines.append(f"- {role.name} — {role.description}")
        return "\n".join(lines)

    def _require_role(self, role_name: str) -> AgentRole:
        role = self._known.get(role_name)
        if role is None:
            raise RoleError(f"未知角色: {role_name!r}")
        return role

    # —— 委派入口 ——
    async def delegate(
        self,
        kind: str,
        prompt: str,
        *,
        role: str | None = None,
        background: bool = False,
        timeout: float | None = None,
    ) -> ToolResult:
        """统一委派入口。defined 需 role；fork 忽略 role 且强制后台（F4）。"""
        try:
            role_obj = None
            if kind == "defined":
                if not role:
                    return ToolResult(call_id="", name="agent",
                                      error="定义式委派需指定 role")
                role_obj = self._require_role(role)
            elif kind != "fork":
                return ToolResult(call_id="", name="agent",
                                  error=f"未知委派类型: {kind!r}（应为 defined/fork）")
            if not prompt or not prompt.strip():
                return ToolResult(call_id="", name="agent", error="委派需提供 prompt")

            force_background = background or kind == "fork"
            if force_background and len(self._running) >= self._max_concurrent:
                return ToolResult(
                    call_id="", name="agent",
                    error=f"后台任务并发已满({self._max_concurrent})，请稍后再试")

            depth = _depth_ctx.get()
            task = SubAgentTask(
                task_id=self._next_id,
                kind=kind,
                role=role_obj.name if role_obj else None,
                status="running",
                prompt=prompt,
                created_at=time.time(),
            )
            self._next_id += 1
            self._tasks[task.task_id] = task

            if force_background:
                return self._spawn_background(task, role_obj, depth)
            return await self._run_foreground(task, role_obj, depth, timeout)
        except RoleError as e:
            return ToolResult(call_id="", name="agent", error=str(e))
        except Exception as e:  # 故障隔离（N2）：委派层异常不外抛
            logger.exception("delegate 异常")
            return ToolResult(call_id="", name="agent", error=f"委派异常: {e}")

    async def _run_foreground(
        self, task: SubAgentTask, role_obj, depth: int, timeout: float | None,
    ) -> ToolResult:
        """前台执行：await 子循环；超时转后台（D11）；/bg 手动切（D12）。"""
        sub_task = asyncio.ensure_future(self._run_one(task, role_obj, depth))
        marker = asyncio.Event()
        self._foreground[task.task_id] = (sub_task, marker)
        # marker 等待协程：/bg 触发 → 转后台
        marker_waiter = asyncio.ensure_future(marker.wait())
        try:
            done, _ = await asyncio.wait(
                {sub_task, marker_waiter},
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED)
            if sub_task in done:
                sub_task.result()  # 触发异常传播（_run_one 已捕获，正常不抛）
                if task.status == "failed":
                    return ToolResult(call_id="", name="agent",
                                      error=task.error or "子 Agent 失败")
                if task.status == "cancelled":
                    return ToolResult(call_id="", name="agent", error="子 Agent 已取消")
                task.status = "done"
                return ToolResult(call_id="", name="agent",
                                  output=task.summary or "(无输出)")
            # marker 触发（/bg 手动切）或超时：转后台继续跑（D11/D12）
            task.status = "backgrounded"
            self._running.add(task.task_id)
            sub_task.add_done_callback(lambda _t: self._on_background_done(task, _t))
            if marker.is_set():
                reason = "已转后台（用户按 Ctrl+B 手动切换，非超时、非系统默认）"
            else:
                reason = "已超时转后台（系统自动，非用户操作）"
            return ToolResult(
                call_id="", name="agent",
                output=(f"{reason}，task_id={task.task_id}，状态=running。"
                        "请只回复一句「任务已转后台，完成后会通知」，不要展开分析。"))
        finally:
            marker_waiter.cancel()
            self._foreground.pop(task.task_id, None)

    def _spawn_background(
        self, task: SubAgentTask, role_obj, depth: int,
    ) -> ToolResult:
        """后台启动：create_task 执行，立即返回占位结果（F8）。"""
        self._running.add(task.task_id)
        sub_task = asyncio.ensure_future(self._run_one(task, role_obj, depth))
        sub_task.add_done_callback(lambda _t: self._on_background_done(task, _t))
        return ToolResult(
            call_id="", name="agent",
            output=(f"已转后台，task_id={task.task_id}，状态=running。"
                    "请只回复一句「任务已转后台，完成后会通知」，不要展开分析。"))

    async def _run_one(self, task: SubAgentTask, role_obj, depth: int):
        """执行子 Agent 并登记结果/用量/状态。"""
        token = _depth_ctx.set(depth + 1)
        try:
            if task.kind == "defined":
                result = await run_role_agent(
                    role_obj, task.prompt, depth=depth + 1, shared=self._shared)
            else:
                parent_names = frozenset(self._parent_visible())
                parent_msgs = self._parent_msgs()
                result = await run_fork_agent(
                    task.prompt, parent_msgs=parent_msgs,
                    parent_names=parent_names, depth=depth + 1, shared=self._shared)
            task.summary = result.summary
            task.usage = result.usage
            task.entries = tuple(result.entries)
            task.status = "done"
        except SubAgentError as e:
            task.error = str(e)
            # 失败也保留已产生的部分轨迹（F11）
            task.entries = tuple(getattr(e, "entries", None) or ())
            task.status = "failed"
        except asyncio.CancelledError:
            task.status = "cancelled"
            raise
        except Exception as e:  # 故障隔离（N2）
            logger.exception("subagent task %d 异常", task.task_id)
            task.error = f"内部异常: {e}"
            task.status = "failed"
        finally:
            _depth_ctx.reset(token)
            self._running.discard(task.task_id)
        return task.summary, task.usage

    def _on_background_done(self, task: SubAgentTask, fut: asyncio.Task) -> None:
        """后台任务完成回调：记日志 + on_task_done（main 接线回灌主对话）。"""
        try:
            if fut.cancelled():
                task.status = "cancelled"
            self._running.discard(task.task_id)
            if task.status == "failed":
                logger.warning("subagent #%d %s failed: %s",
                               task.task_id, task.kind, task.error)
            else:
                logger.info(
                    "subagent #%d %s done: status=%s chars=%d",
                    task.task_id, task.kind, task.status, len(task.summary))
            if self._on_task_done is not None:
                self._on_task_done(task)
        except Exception as e:
            logger.exception("subagent done 回调异常: %s", e)

    # —— 控制 ——
    def cancel(self, task_id: int) -> bool:
        """取消后台任务（F10）。"""
        task = self._tasks.get(task_id)
        if task is None or task.task_id not in self._running:
            return False
        task.status = "cancelled"
        self._running.discard(task_id)
        logger.info("subagent #%d cancelled", task_id)
        return True

    def background_now(self, task_id: int) -> bool:
        """手动切后台（/bg、快捷键）：前台等待中的任务解除 await、转后台继续跑（D12）。"""
        entry = self._foreground.get(task_id)
        if entry is None:
            return False
        entry[1].set()  # 触发 marker → _run_foreground 转后台
        return True

    def foreground_task_ids(self) -> list[int]:
        """当前前台等待中的任务 id（TUI 快捷键切后台用；前台运行中输入框禁用）。"""
        return list(self._foreground.keys())

    # —— 接线（main/app 在 App 就绪后注入，避免构造期循环依赖）——
    def set_on_task_done(self, cb: Callable[[SubAgentTask], None] | None) -> None:
        self._on_task_done = cb

    def set_parent_msgs(self, getter: Callable[[], list[dict]]) -> None:
        """主对话 _msgs 取数回调（fork 继承父历史）。"""
        self._parent_msgs = getter

    def set_parent_visible(self, getter: Callable[[], set[str]]) -> None:
        """父当前可见工具名取数回调（fork 继承父工具集）。"""
        self._parent_visible = getter
