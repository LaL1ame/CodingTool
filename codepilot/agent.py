"""Agent 层：ReAct 循环编排 — 多轮「调用模型 → 执行工具 → 回灌」直到完成。"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from pathlib import Path

from codepilot.permissions import PermissionManager
from codepilot.protocols import Delta, ToolCall, ToolResult
from codepilot.prompt import EnvContext, build_system_prompt
from codepilot.providers import BaseProvider
from codepilot.tools.registry import ToolRegistry


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

    # —— 指纹(F3.4 lex 级)——
    @staticmethod
    def _fingerprint(tc: ToolCall) -> str:
        # 确定性:工具名 + 参数的稳定 repr。dict 顺序无关 → 排序后 repr。
        arg = tc.arguments
        if isinstance(arg, dict):
            sig = repr(sorted(arg.items(), key=lambda kv: str(kv[0])))
        else:
            sig = repr(arg)
        return f"{tc.name}|{hash(sig)}"

    def record(self, tool_calls: list["ToolCall"], had_text: bool) -> bool:
        """本轮评估。返回 True=触发软 stall_hint(继续跑,非停)。"""
        if not tool_calls or had_text:
            # 无工具调用(将 done)或有文本进展 → 有前进,不判空转
            self._reset_progress()
            return False
        fps = [self._fingerprint(tc) for tc in tool_calls]
        self._recent.append(tuple(fps))
        # 窗口内:同指纹(整轮调用集合)出现次数
        dup = sum(1 for e in self._recent if e == tuple(fps))
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
        self._plan_mode = False
        self._stall_enabled = stall_enabled
        self._stall_msg = stall_msg
        self._stall = _StallDetector(repeat_k=stall_repeat_k, window=stall_window) if (
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

    def _build_tools(self) -> list[dict] | None:
        tools = self._registry.list_read_only() if self._plan_mode else self._registry.list_all()
        if not tools:
            return None
        if self._protocol == "anthropic":
            return self._registry.to_anthropic_tools(tools)
        else:
            return self._registry.to_openai_tools(tools)

    async def run(self, messages: list[dict]) -> AsyncIterator[Delta]:
        self._cancel_event.clear()  # 重置取消标志，确保新循环不受上次取消影响
        tools = self._build_tools()
        consecutive_unknown = 0

        for round_num in range(1, self._max_rounds + 1):
            yield Delta(round=round_num, max_rounds=self._max_rounds)
            self._logger.info("round %d/%d start", round_num, self._max_rounds)

            # —— 每轮重建分层系统提示（稳定前缀 + 动态尾块）——
            memory_blocks = self._memory_injector() if self._memory_injector else []
            sp = build_system_prompt(
                self._env or EnvContext(cwd=str(self._workspace), date=""),
                plan_mode=self._plan_mode,
                round_num=round_num,
                refresh_every=self._refresh_every,
                memory_blocks=memory_blocks,
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
                yield Delta(done=True)
                return

            # —— 连续未知工具检测（安全网）——
            names = [tc.name for tc in tool_calls]
            self._logger.info("round %d tools: %s", round_num, ", ".join(names))
            stall_hint = False
            if any(self._registry.get(tc.name) is None for tc in tool_calls):
                consecutive_unknown += 1
            else:
                consecutive_unknown = 0
                # —— state10: 空转软守卫评估本轮(F3,仅当无未知工具) ——
                if self._stall is not None:
                    stall_hint = self._stall.record(
                        tool_calls, had_text=bool("".join(text_parts)))

            if consecutive_unknown >= self._max_unknown:
                self._logger.warning(
                    "round %d stop: %d consecutive unknown tools (%s)",
                    round_num, self._max_unknown, ", ".join(names),
                )
                yield Delta(paused=True, pause_reason="unknown_tool",
                            max_rounds=self._max_rounds,
                            error=None)  # N1:非 error,友好提示由 UI 呈现
                return

            # —— state10: 空转软提示(仍继续跑)→ 持续空转则硬停 ——
            if stall_hint:
                self._logger.warning("round %d stall-hint: repeated no-progress", round_num)
                yield Delta(stall_hint=self._stall_msg, round=round_num,
                            max_rounds=self._max_rounds)
            if self._stall is not None and self._stall.should_stop():
                self._logger.warning("round %d stall-stop: persistent repetition", round_num)
                yield Delta(paused=True, pause_reason="stall",
                            resume_round=round_num, max_rounds=self._max_rounds)
                return

            # —— 有工具调用 → 回灌助手消息 → 执行 → 回灌结果 → 下一轮 ——
            self._append_assistant(messages, "".join(text_parts), tool_calls)
            results = await self._execute_batched(tool_calls)
            # 层1:可选注入 context_manager 时,对每个工具结果做阈值存盘(替身回灌)
            if self._context_manager is not None:
                results = [await self._context_manager.prep_tool_result(r) for r in results]
            for r in results:
                if r.error:
                    self._logger.error("  tool %s error: %s", r.name, r.error)
                else:
                    self._logger.info("  tool %s ok: %s", r.name, (r.output or "")[:80])
                yield Delta(tool_result=r)
            self._append_tool_results(messages, results)

            if self._cancel_event.is_set():
                self._logger.info("round %d cancelled after batch (user)", round_num)
                yield Delta(cancelled=True)
                return

        self._logger.warning("stop: iteration limit %d reached", self._max_rounds)
        # F2: 达上限是可续接检查点,非 error —— 调用方可沿用同一 _msgs 续跑。
        yield Delta(paused=True, pause_reason="limit",
                    resume_round=self._max_rounds, max_rounds=self._max_rounds)

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
        try:
            kwargs = dict(tc.arguments)
            result = await asyncio.wait_for(tool.execute(**kwargs), timeout=self._timeout)
            if not result.call_id:
                result.call_id = tc.id
            if not result.name:
                result.name = tc.name
            return result
        except asyncio.CancelledError:
            return ToolResult(call_id=tc.id, name=tc.name, error="已取消")
        except asyncio.TimeoutError:
            return ToolResult(call_id=tc.id, name=tc.name, error=f"工具执行超时 ({self._timeout:.0f}s)")
        except Exception as e:
            return ToolResult(call_id=tc.id, name=tc.name, error=f"工具执行异常: {e}")

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
