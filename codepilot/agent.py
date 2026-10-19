"""Agent 层：ReAct 循环编排 — 多轮「调用模型 → 执行工具 → 回灌」直到完成。"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import TYPE_CHECKING

from codepilot.permissions import PermissionManager
from codepilot.protocols import Delta, ToolCall, ToolResult
from codepilot.prompt import EnvContext, build_system_prompt
from codepilot.providers import BaseProvider
from codepilot.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from codepilot.tools.base import Tool


class _StallDetector:
    """空转软守卫(F3):lex 级检测「重复调用同一工具+参数且无文本进展」。

    基于工具名 + 参数 repr 的确定性指纹,不依赖语义判断。纯内存、O(回合)。
    设计:
      record()   本轮评估;有工具调用且无文本进展时,若最近窗口内同指纹出现
                 >= repeat_k 次 → 返回 True(触发软 stall_hint,仍继续跑)。
      should_stop() 持续重复(窗口内重复指纹占比高) → 硬停 paused(stall)。
    有文本进展(had_text=True)本轮不判空转 —— 慢但前进不误伤(N1.2)。
    """

    def __init__(self, *, repeat_k: int, window: int) -> None:
        self._repeat_k = max(1, repeat_k)
        self._window = max(1, window)
        self._recent: list[str] = []       # 最近窗口内的指纹序列
        self._stall_repeated = False       # 已被"硬停"判定(连续空转)

    # —— 指纹(F3.4 结果级)——
    @staticmethod
    def _std_sig(value: object) -> str:
        # dict 顺序无关 → 排序后 repr；其余稳定 repr。
        if isinstance(value, dict):
            return repr(sorted(value.items(), key=lambda kv: str(kv[0])))
        return repr(value)

    def _fingerprint(self, tc: ToolCall, result: "ToolResult") -> str:
        # 工具名 + 参数 稳定签名 + 「原始结果」签名。结果参与指纹是判断
        # 「真空转 vs 合法轮询」的关键：外部世界在变(结果不同)则不应判空转。
        arg = self._std_sig(tc.arguments)
        if result.error:
            res_sig = f"err:{result.error}"
        else:
            res_sig = f"out:{self._std_sig(result.output)}"
        return f"{tc.name}|{hash(arg)}|{hash(res_sig)}"

    def record(
        self,
        tool_calls: list["ToolCall"],
        results: list["ToolResult"],
        had_text: bool = False,
    ) -> bool:
        """本轮评估(工具已执行、拿到原始结果后)。返回 True=触发软 stall_hint。

        state19 设计补充 A：结果感知。同参数但结果变化 ⇒ 外部世界在推进(
        Lead 轮询任务板/成员状态) ⇒ 视为合法轮询,重置进度不休;参数与结果都
        相同 ⇒ 真死循环 ⇒ 累积并可能硬停。
        """
        if not tool_calls or had_text:
            # 无工具调用(将 done)或有文本进展 → 有前进,不判空转
            self._reset_progress()
            return False
        if len(results) < len(tool_calls):
            results = [*results, *(ToolResult(call_id="", name="")
                                   for _ in range(len(tool_calls) - len(results)))]
        fps = [self._fingerprint(tc, res)
               for tc, res in zip(tool_calls, results)]
        new_key = tuple(fps)
        # 结果与上一轮不同 → 外部世界在变 → 重置(不是空转)
        if self._recent and self._recent[-1] != new_key:
            self._recent.clear()
        self._recent.append(new_key)
        # 窗口内:同指纹(整轮调用集合)连续出现次数
        dup = sum(1 for e in self._recent[-self._window:] if e == new_key)
        return dup >= self._repeat_k

    def should_stop(self) -> bool:
        """持续空转(窗口内同一指纹占比超高)→ 硬停。"""
        if not self._recent:
            return False
        if self._stall_repeated:
            return True
        last = self._recent[-1]
        # 最近窗口内,出现次数 >= repeat_k 且超过半数 → 持续空转
        count = sum(1 for e in self._recent[-self._window:] if e == last)
        if count >= self._repeat_k and count >= (self._window + 1) // 2:
            self._stall_repeated = True
            return True
        return False

    def _reset_progress(self) -> None:
        # 有前进:清空窗口,重新计时
        self._recent.clear()
        self._stall_repeated = False


class _SideStall:
    """state19 设计补充 A·N2：副作用工具的空转重放保护。

    结果级 stall 移到执行后，会使「空转的那轮副作用调用」被真正执行才停，
    破坏「续接不重放副作用」(N2)。故对含副作用工具的轮次，在执行前用
    参数级指纹拦截连续重复调用 —— 副作用绝不因空转被重放一次。
    """

    def __init__(self, repeat_k: int = 2) -> None:
        self._repeat_k = repeat_k
        self._recent: list[tuple[str, ...]] = []

    def stop_on_repeat(self, tool_calls: list["ToolCall"], had_text: bool) -> bool:
        if had_text:
            # 有文本解释/进展 → 不判副作用空转
            self._recent.clear()
            return False
        fps = tuple(
            f"{tc.name}|{hash(_StallDetector._std_sig(tc.arguments))}"
            for tc in tool_calls)
        if self._recent and self._recent[-1] == fps:
            self._recent.append(fps)
        else:
            self._recent = [fps]   # 工具/参数组合变化 → 换新工作,重置
        dup = sum(1 for e in self._recent if e == fps)
        return dup >= self._repeat_k


class Agent:
    def __init__(
        self,
        provider: BaseProvider,
        registry: ToolRegistry,
        workspace: Path,
        timeout: float = 120.0,
        max_rounds: int = 18,
        max_unknown: int = 2,
        env: EnvContext | None = None,
        refresh_every: int = 3,
        permission: PermissionManager | None = None,
        context_manager=None,
        memory_injector: Callable[[], list[str]] | None = None,
        skill_injector: Callable[[], list[str]] | None = None,
        skill_whitelist: Callable[[], set[str] | None] | None = None,
        # state17: 可选工具过滤回调（子 Agent 多层防线注入点）。在 skill 白名单 +
        # plan 只读过滤之后调用；None 时行为与现状一致（向后兼容）。
        tool_filter: Callable[[list[Tool]], list[Tool]] | None = None,
        hooks=None,
        hook_injector: Callable[[], list[str]] | None = None,
        # state17: 可委派子 Agent 角色名册的 volatile 块（让模型知道有哪些角色）。
        subagent_injector: Callable[[], list[str]] | None = None,
        stall_enabled: bool = True,
        stall_repeat_k: int = 2,
        stall_window: int = 3,
        stall_msg: str = "检测到重复调用且无进展,建议更换工具或参数,或重新明确目标。",
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._workspace = workspace
        self._timeout = timeout
        self._max_rounds = max_rounds
        self._max_unknown = max_unknown
        self._env = env
        self._refresh_every = refresh_every
        self._permission = permission
        self._context_manager = context_manager  # 可选注入(层1压缩,state9)
        # state13:可选记忆注入器,每轮产出 volatile 尾块(指令+索引)。Agent 只
        # 把他当「不透明 volatile 块」,不感知记忆内部 → 低耦合。
        self._memory_injector = memory_injector
        # state15:可选项技能注入器(阶段一名册+激活钉住)与白名单回调(收窄工具)。
        # 同样是「不透明 volatile 块 / 工具名集合回调」,Agent 不感知 skill 内部。
        self._skill_injector = skill_injector
        self._skill_whitelist = skill_whitelist
        # state17: 子 Agent 多层防线注入点（None 时行为与现状一致）
        self._tool_filter = tool_filter
        # state16:可选 Hook 引擎与注入器。Agent 只当它们是「不透明回调」:
        # engine 暴露 fire/fire_pre_tool,injector 暴露 hook_blocks() ——
        # 不感知 Hook 内部,零配置时全部短路(N5)。
        self._hooks = hooks
        self._hook_injector = hook_injector
        self._subagent_injector = subagent_injector
        self._plan_mode = False
        self._stall_enabled = stall_enabled
        self._stall_msg = stall_msg
        self._stall = _StallDetector(repeat_k=stall_repeat_k, window=stall_window) if (
            stall_enabled) else None
        # state19 A·N2：仅处理含副作用工具的轮次，执行前拦截连续重复(防空转重放副作用)。
        self._side_stall = _SideStall(repeat_k=stall_repeat_k) if (
            stall_enabled) else None
        self._cancel_event = asyncio.Event()
        self._tool_tasks: set[asyncio.Task] = set()
        self._logger = logging.getLogger("codepilot.agent")

    @property
    def _protocol(self) -> str:
        return self._provider.config.protocol.lower()

    def set_plan_mode(self, on: bool) -> None:
        """切换计划模式：只读工具 + 按轮注入 plan 指令（builder 侧）。"""
        self._plan_mode = on

    def cancel(self) -> None:
        """请求取消当前循环：置标志并中断进行中的工具任务。"""
        self._cancel_event.set()
        for task in list(self._tool_tasks):
            task.cancel()

    def visible_tool_names(self) -> set[str]:
        """当前可见工具名集合（state17：Fork 式继承父工具集用）。

        复用 _build_tools 的过滤链（skill 白名单/plan/tool_filter），再按协议
        形态提取名字——不改动既有过滤语义。
        """
        tools = self._build_tools()
        if not tools:
            return set()
        if self._protocol == "anthropic":
            return {t.get("name", "") for t in tools}
        return {t.get("function", {}).get("name", "") for t in tools}

    def _build_tools(self) -> list[dict] | None:
        # state15: 可见工具 = 全量 ∩(白名单∪系统工具) ∩(plan仅只读)。
        all_tools = self._registry.list_all()
        if self._skill_whitelist is not None:
            wl = self._skill_whitelist()
            if wl is not None:
                # 白名单收窄：只留白名单内 + 系统级工具（逃生门，恒可见）
                all_tools = [t for t in all_tools
                             if t.name in wl or t.system_exempt]
        if self._plan_mode:
            all_tools = [t for t in all_tools if not t.side_effect]
        # state17: 子 Agent 多层防线（在 skill 白名单 + plan 只读之后叠加）
        if self._tool_filter is not None:
            all_tools = self._tool_filter(all_tools)
        if not all_tools:
            return None
        if self._protocol == "anthropic":
            return self._registry.to_anthropic_tools(all_tools)
        else:
            return self._registry.to_openai_tools(all_tools)

    async def run(self, messages: list[dict]) -> AsyncIterator[Delta]:
        self._cancel_event.clear()  # 重置取消标志，确保新循环不受上次取消影响
        # protocol-trace R2/R3：发请求前清洗历史。端到端流程里 assistant 与
        # tool_result 总是成对追加，唯一破裂点是用户取消（Esc）留下 dangling
        # tool_use，或 resume 恢复的损坏历史 —— 补齐「已取消」结果即恢复配对。
        self._sanitize_tail(messages)
        tools = self._build_tools()
        consecutive_unknown = 0

        for round_num in range(1, self._max_rounds + 1):
            yield Delta(round=round_num, max_rounds=self._max_rounds)
            self._logger.info("round %d/%d start", round_num, self._max_rounds)

            # state16: round_start 发射点（在本轮提示重建前,注入当轮即可见）
            if self._hooks is not None:
                await self._hooks.fire("round_start", {"round": round_num})

            # —— 每轮重建分层系统提示（稳定前缀 + 动态尾块）——
            memory_blocks = self._memory_injector() if self._memory_injector else []
            skill_blocks = self._skill_injector() if self._skill_injector else []
            hook_blocks = self._hook_injector() if self._hook_injector else []
            subagent_blocks = (
                self._subagent_injector() if self._subagent_injector else [])
            sp = build_system_prompt(
                self._env or EnvContext(cwd=str(self._workspace), date=""),
                plan_mode=self._plan_mode,
                round_num=round_num,
                refresh_every=self._refresh_every,
                memory_blocks=memory_blocks,
                skill_blocks=skill_blocks,
                hook_blocks=hook_blocks,
                subagent_blocks=subagent_blocks,
            )
            if self._plan_mode:
                n_plan = sum(1 for b in sp.volatile if "session_instruction" in b)
                self._logger.info("round %d plan-inject blocks: %d", round_num, n_plan)

            # —— 单轮：流式收集（双路：实时 yield + 累积完整文本）——
            text_parts: list[str] = []
            tool_calls: list[ToolCall] = []
            async for delta in self._provider.stream(messages, tools=tools, system=sp):
                if delta.error:
                    yield delta
                    return
                if delta.text:
                    text_parts.append(delta.text)
                    yield delta
                if delta.tool_calls is not None:
                    tool_calls = delta.tool_calls
                    yield delta
                if delta.usage is not None:
                    if self._context_manager is not None:
                        self._context_manager.on_usage_anchor(delta.usage.input_tokens)
                    if delta.usage.cache_read is not None:
                        self._logger.info(
                            "round %d cache read=%d creation=%d",
                            round_num, delta.usage.cache_read, delta.usage.cache_creation,
                        )
                    yield delta

            if self._cancel_event.is_set():
                self._logger.info("round %d cancelled (user)", round_num)
                yield Delta(cancelled=True)
                return

            # —— 无工具调用 → 最终回复，结束 ——
            if not tool_calls:
                self._logger.info(
                    "round %d done: no tool calls, text=%s",
                    round_num, "".join(text_parts)[:80],
                )
                self._append_assistant(messages, "".join(text_parts), [])
                await self._emit_stop("done")
                yield Delta(done=True)
                return

            # —— 连续未知工具检测（安全网）——
            names = [tc.name for tc in tool_calls]
            self._logger.info("round %d tools: %s", round_num, ", ".join(names))
            if any(self._registry.get(tc.name) is None for tc in tool_calls):
                consecutive_unknown += 1
            else:
                consecutive_unknown = 0

            if consecutive_unknown >= self._max_unknown:
                self._logger.warning(
                    "round %d stop: %d consecutive unknown tools (%s)",
                    round_num, self._max_unknown, ", ".join(names),
                )
                await self._emit_stop("unknown_tool")
                yield Delta(paused=True, pause_reason="unknown_tool",
                            max_rounds=self._max_rounds,
                            error=None)  # N1:非 error,友好提示由 UI 呈现
                return

            # —— 有工具调用 → 回灌助手消息 → 执行 → 取原始结果 ——
            # state19 A·N2：含副作用工具的轮次，执行前拦截连续重复调用(防空转重放副作用)。
            if (self._side_stall is not None
                    and any(self._is_side_effect(tc) for tc in tool_calls)
                    and self._side_stall.stop_on_repeat(
                        tool_calls, had_text=bool("".join(text_parts)))):
                self._logger.warning(
                    "round %d side-effect repeat halt (防空转重放副作用)",
                    round_num)
                await self._emit_stop("stall")
                yield Delta(paused=True, pause_reason="stall",
                            resume_round=round_num, max_rounds=self._max_rounds)
                return
            self._append_assistant(messages, "".join(text_parts), tool_calls)
            raw_results = await self._execute_batched(tool_calls)
            # state19 设计补充 A：空转评估移到执行后、拿到原始结果时(结果感知)。
            # 用 prep/offload 之前的 raw_results 做指纹,避免层1替身恒定导致误判。
            stall_hint = False
            if self._stall is not None:
                stall_hint = self._stall.record(
                    tool_calls, raw_results,
                    had_text=bool("".join(text_parts)))

            # 层1:可选注入 context_manager 时,对每个工具结果做阈值存盘(替身回灌)
            if self._context_manager is not None:
                results = [await self._context_manager.prep_tool_result(r)
                           for r in raw_results]
            else:
                results = raw_results
            for r in results:
                if r.error:
                    self._logger.error("  tool %s error: %s", r.name, r.error)
                else:
                    self._logger.info("  tool %s ok: %s", r.name, (r.output or "")[:80])
                yield Delta(tool_result=r)
            self._append_tool_results(messages, results)

            # —— state10: 空转软提示(仍继续跑)→ 持续空转则硬停 ——
            # 放在 append_tool_results 之后:即使硬停,本轮结果也已回灌,不成 dangling。
            if stall_hint:
                self._logger.warning("round %d stall-hint: repeated no-progress", round_num)
                yield Delta(stall_hint=self._stall_msg, round=round_num,
                            max_rounds=self._max_rounds)
            if self._stall is not None and self._stall.should_stop():
                self._logger.warning("round %d stall-stop: persistent repetition", round_num)
                await self._emit_stop("stall")
                yield Delta(paused=True, pause_reason="stall",
                            resume_round=round_num, max_rounds=self._max_rounds)
                return

            if self._cancel_event.is_set():
                self._logger.info("round %d cancelled after batch (user)", round_num)
                yield Delta(cancelled=True)
                return

        self._logger.warning("stop: iteration limit %d reached", self._max_rounds)
        # F2: 达上限是可续接检查点,非 error —— 调用方可沿用同一 _msgs 续跑。
        await self._emit_stop("limit")
        yield Delta(paused=True, pause_reason="limit",
                    resume_round=self._max_rounds, max_rounds=self._max_rounds)

    async def _emit_stop(self, reason: str) -> None:
        """state16: stop 发射点 —— done/limit/stall/unknown_tool 四出口收拢。"""
        if self._hooks is not None:
            await self._hooks.fire("stop", {"reason": reason})

    def _is_side_effect(self, tc: ToolCall) -> bool:
        tool = self._registry.get(tc.name)
        return tool is not None and tool.side_effect

    async def _execute_one(self, tc: ToolCall) -> ToolResult:
        tool = self._registry.get(tc.name)
        if tool is None:
            return ToolResult(call_id=tc.id, name=tc.name, error=f"未知工具: {tc.name}")
        if self._permission is not None:
            decision = await self._permission.check(tc.name, dict(tc.arguments))
            if not decision.allow:
                self._logger.warning("deny %s: %s", tc.name, decision.reason)
                return ToolResult(call_id=tc.id, name=tc.name, error=f"权限拒绝: {decision.reason}")
        # state16: tool_pre 发射点（权限通过之后、执行之前;拦截 → 原因当工具结果回灌）
        if self._hooks is not None:
            reason = await self._hooks.fire_pre_tool(tc.name, dict(tc.arguments))
            if reason is not None:
                self._logger.info("hook 拦截 %s: %s", tc.name, reason)
                return ToolResult(call_id=tc.id, name=tc.name, error=f"Hook 拦截: {reason}")
        try:
            kwargs = dict(tc.arguments)
            result = await asyncio.wait_for(tool.execute(**kwargs), timeout=self._timeout)
            if not result.call_id:
                result.call_id = tc.id
            if not result.name:
                result.name = tc.name
        except asyncio.CancelledError:
            return ToolResult(call_id=tc.id, name=tc.name, error="已取消")
        except asyncio.TimeoutError:
            result = ToolResult(call_id=tc.id, name=tc.name, error=f"工具执行超时 ({self._timeout:.0f}s)")
        except Exception as e:
            result = ToolResult(call_id=tc.id, name=tc.name, error=f"工具执行异常: {e}")
        # state16: tool_post 发射点（权限拒绝/未知工具/取消走不到这里）
        if self._hooks is not None:
            await self._hooks.fire(
                "tool_post",
                {"tool": tc.name, **dict(tc.arguments), "ok": result.error is None})
        return result

    @staticmethod
    def _assistant_tool_ids(msg: dict, protocol: str) -> list[str]:
        """取一条 assistant 消息里的工具调用 id（按协议形态）。"""
        if protocol == "anthropic":
            content = msg.get("content")
            if isinstance(content, list):
                return [b.get("id") for b in content
                        if isinstance(b, dict) and b.get("type") == "tool_use"]
            return []
        calls = msg.get("tool_calls")
        if isinstance(calls, list):
            return [c.get("id") for c in calls if isinstance(c, dict)]
        return []

    @staticmethod
    def _collect_pair_ids(messages: list[dict], after: int, protocol: str) -> set[str]:
        """收集紧随 `after` 之后、与 assistant tool_use 配对的 tool_result id。

        anthropic：下一条 user 消息里的 tool_result.tool_use_id 集合。
        openai：`after` 起连续 role=tool 消息的 tool_call_id 集合。
        """
        j = after
        got: set[str] = set()
        if protocol == "anthropic":
            if j < len(messages) and messages[j].get("role") == "user":
                content = messages[j].get("content")
                if isinstance(content, list):
                    for b in content:
                        if (isinstance(b, dict) and b.get("type") == "tool_result"
                                and b.get("tool_use_id")):
                            got.add(b["tool_use_id"])
            return got
        while j < len(messages) and messages[j].get("role") == "tool":
            cid = messages[j].get("tool_call_id")
            if cid:
                got.add(cid)
            j += 1
        return got

    def _sanitize_tail(self, messages: list[dict]) -> None:
        """兜底：末尾 assistant 的 tool_use/tool_calls 缺紧随 tool_result 时补齐。"""
        i = len(messages) - 1
        while i >= 0:
            if messages[i].get("role") != "assistant":
                i -= 1
                continue
            ids = self._assistant_tool_ids(messages[i], self._protocol)
            if ids:
                got = self._collect_pair_ids(messages, i + 1, self._protocol)
                missing = [cid for cid in ids if cid not in got]
                if missing:
                    self._logger.warning(
                        "清洗历史：为 %d 个 dangling tool_use 补齐已取消结果",
                        len(missing))
                    self._append_tool_results(messages, [
                        ToolResult(call_id=cid, name="", error="已取消")
                        for cid in missing])
            break
        return

    async def _execute_batched(self, tool_calls: list[ToolCall]) -> list[ToolResult]:
        """按安全性分批：副作用工具为「栅栏」（串行），栅栏间的只读工具并发。"""
        groups: list[tuple[bool, list[ToolCall]]] = []
        i = 0
        while i < len(tool_calls):
            if self._is_side_effect(tool_calls[i]):
                groups.append((False, [tool_calls[i]]))
                i += 1
            else:
                batch: list[ToolCall] = []
                while i < len(tool_calls) and not self._is_side_effect(tool_calls[i]):
                    batch.append(tool_calls[i])
                    i += 1
                groups.append((True, batch))

        results_by_id: dict[str, ToolResult] = {}
        for is_concurrent, batch in groups:
            if is_concurrent:
                tasks = [asyncio.create_task(self._execute_one(tc)) for tc in batch]
                self._tool_tasks.update(tasks)
                try:
                    res = await asyncio.gather(*tasks)
                finally:
                    self._tool_tasks.difference_update(tasks)
                for tc, r in zip(batch, res):
                    results_by_id[tc.id] = r
            else:
                for tc in batch:
                    task = asyncio.create_task(self._execute_one(tc))
                    self._tool_tasks.add(task)
                    try:
                        results_by_id[tc.id] = await task
                    finally:
                        self._tool_tasks.discard(task)

        return [results_by_id[tc.id] for tc in tool_calls]

    def _append_assistant(self, messages: list[dict], text: str, tool_calls: list[ToolCall]) -> None:
        """回灌一条助手消息（文本 + 可选工具调用），按协议原生格式。"""
        if self._protocol == "anthropic":
            content: list[dict] = []
            if text:
                content.append({"type": "text", "text": text})
            content.extend([
                {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments}
                for tc in tool_calls
            ])
            messages.append({"role": "assistant", "content": content})
        else:
            msg: dict = {"role": "assistant", "content": text or None}
            if tool_calls:
                msg["tool_calls"] = [
                    {
                        "id": tc.id, "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)},
                    }
                    for tc in tool_calls
                ]
            messages.append(msg)

    def _append_tool_results(self, messages: list[dict], results: list[ToolResult]) -> None:
        """回灌工具执行结果，按协议原生格式。"""
        if self._protocol == "anthropic":
            messages.append({"role": "user", "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": r.call_id,
                    "content": r.error if r.error else r.output or "",
                    **({"is_error": True} if r.error else {}),
                }
                for r in results
            ]})
        else:
            for r in results:
                messages.append({
                    "role": "tool", "tool_call_id": r.call_id,
                    "content": r.error if r.error else r.output or "",
                })
