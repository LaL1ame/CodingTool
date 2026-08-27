"""Agent 层：ReAct 循环编排 — 多轮「调用模型 → 执行工具 → 回灌」直到完成。"""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

from codepilot.protocols import Delta, ToolCall, ToolResult
from codepilot.providers import BaseProvider
from codepilot.tools.registry import ToolRegistry

SYSTEM_PROMPT = (
    "You are CodePilot, a CLI AI coding assistant. "
    "You help with programming tasks by reading files, searching code, editing files, "
    "and running commands using your available tools. "
    "Work autonomously: use tools as needed, inspect results, and adjust until the task is done. "
    "Be concise but thorough."
)

PLAN_MODE_SUFFIX = (
    "\n\n[Plan Mode] 你当前处于计划模式：只能使用只读工具（read/glob/grep）探索现状，"
    "不要修改文件或执行命令。请分析代码并给出清晰、可执行的计划。"
)


class Agent:
    def __init__(
        self,
        provider: BaseProvider,
        registry: ToolRegistry,
        workspace: Path,
        timeout: float = 120.0,
        confirm_callback: Callable[[str], Awaitable[bool]] | None = None,
        max_rounds: int = 10,
        max_unknown: int = 2,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._workspace = workspace
        self._timeout = timeout
        self._confirm_callback = confirm_callback
        self._max_rounds = max_rounds
        self._max_unknown = max_unknown
        self._plan_mode = False
        self._cancel_event = asyncio.Event()
        self._tool_tasks: set[asyncio.Task] = set()

    @property
    def _protocol(self) -> str:
        return self._provider.config.protocol.lower()

    def set_plan_mode(self, on: bool) -> None:
        """切换计划模式：只读工具 + 计划提醒提示词。"""
        self._plan_mode = on

    def cancel(self) -> None:
        """请求取消当前循环：置标志并中断进行中的工具任务。"""
        self._cancel_event.set()
        for task in list(self._tool_tasks):
            task.cancel()

    def system_prompt(self) -> str:
        """返回模式感知的系统提示词。"""
        if self._plan_mode:
            return SYSTEM_PROMPT + PLAN_MODE_SUFFIX
        return SYSTEM_PROMPT

    def _build_tools(self) -> list[dict] | None:
        tools = self._registry.list_read_only() if self._plan_mode else self._registry.list_all()
        if not tools:
            return None
        if self._protocol == "anthropic":
            return self._registry.to_anthropic_tools(tools)
        else:
            return self._registry.to_openai_tools(tools)

    async def run(self, messages: list[dict]) -> AsyncIterator[Delta]:
        tools = self._build_tools()
        consecutive_unknown = 0

        for round_num in range(1, self._max_rounds + 1):
            yield Delta(round=round_num, max_rounds=self._max_rounds)

            # —— 单轮：流式收集（双路：实时 yield + 累积完整文本）——
            text_parts: list[str] = []
            tool_calls: list[ToolCall] = []
            async for delta in self._provider.stream(messages, tools=tools):
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
                    yield delta

            if self._cancel_event.is_set():
                yield Delta(cancelled=True)
                return

            # —— 无工具调用 → 最终回复，结束 ——
            if not tool_calls:
                self._append_assistant(messages, "".join(text_parts), [])
                yield Delta(done=True)
                return

            # —— 连续未知工具检测（安全网）——
            if any(self._registry.get(tc.name) is None for tc in tool_calls):
                consecutive_unknown += 1
            else:
                consecutive_unknown = 0

            if consecutive_unknown >= self._max_unknown:
                yield Delta(error=f"连续调用未知工具（{self._max_unknown} 次），已停止")
                return

            # —— 有工具调用 → 回灌助手消息 → 执行 → 回灌结果 → 下一轮 ——
            self._append_assistant(messages, "".join(text_parts), tool_calls)
            results = await self._execute_batched(tool_calls)
            for r in results:
                yield Delta(tool_result=r)
            self._append_tool_results(messages, results)

            if self._cancel_event.is_set():
                yield Delta(cancelled=True)
                return

        yield Delta(error=f"达到迭代上限（{self._max_rounds} 轮），已停止")

    def _is_side_effect(self, tc: ToolCall) -> bool:
        tool = self._registry.get(tc.name)
        return tool is not None and tool.side_effect

    async def _execute_one(self, tc: ToolCall) -> ToolResult:
        tool = self._registry.get(tc.name)
        if tool is None:
            return ToolResult(call_id=tc.id, name=tc.name, error=f"未知工具: {tc.name}")
        try:
            kwargs = dict(tc.arguments)
            if tc.name == "run" and self._confirm_callback is not None:
                kwargs["confirm_callback"] = self._confirm_callback
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
