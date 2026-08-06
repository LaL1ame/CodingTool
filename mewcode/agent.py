"""Agent 层：工具编排 — 单轮对话的工具调用生命周期管理。"""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

from mewcode.protocols import Delta, ToolCall, ToolResult
from mewcode.providers import BaseProvider
from mewcode.tools.registry import ToolRegistry


class Agent:
    def __init__(
        self,
        provider: BaseProvider,
        registry: ToolRegistry,
        workspace: Path,
        timeout: float = 120.0,
        confirm_callback: Callable[[str], Awaitable[bool]] | None = None,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._workspace = workspace
        self._timeout = timeout
        self._confirm_callback = confirm_callback

    @property
    def _protocol(self) -> str:
        return self._provider.config.protocol.lower()

    def _build_tools(self) -> list[dict]:
        if self._protocol == "anthropic":
            return self._registry.to_anthropic_tools()
        else:
            return self._registry.to_openai_tools()

    async def run(self, messages: list[dict]) -> AsyncIterator[Delta]:
        tools = self._build_tools() if self._registry.list_all() else None

        tool_calls_delta: Delta | None = None
        async for delta in self._provider.stream(messages, tools=tools):
            if delta.tool_calls is not None:
                tool_calls_delta = delta
                break
            yield delta

        if tool_calls_delta is None:
            return

        tool_calls = tool_calls_delta.tool_calls or []
        results = await self._execute_parallel(tool_calls)

        for r in results:
            yield Delta(tool_result=r)

        self._inject_tool_results(messages, tool_calls, results)

        async for delta in self._provider.stream(messages, tools=tools):
            if delta.text or delta.done or delta.error:
                yield delta

    async def _execute_parallel(self, tool_calls: list[ToolCall]) -> list[ToolResult]:
        async def execute_one(tc: ToolCall) -> ToolResult:
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
            except asyncio.TimeoutError:
                return ToolResult(call_id=tc.id, name=tc.name, error=f"工具执行超时 ({self._timeout:.0f}s)")
            except Exception as e:
                return ToolResult(call_id=tc.id, name=tc.name, error=f"工具执行异常: {e}")

        tasks = [execute_one(tc) for tc in tool_calls]
        raw = await asyncio.gather(*tasks, return_exceptions=True)
        return [
            ToolResult(call_id=tc.id, name=tc.name, error=f"工具执行异常: {r}")
            if isinstance(r, Exception) else r
            for tc, r in zip(tool_calls, raw)
        ]

    def _inject_tool_results(self, messages, tool_calls, results):
        if self._protocol == "anthropic":
            self._inject_anthropic(messages, tool_calls, results)
        else:
            self._inject_openai(messages, tool_calls, results)

    def _inject_anthropic(self, messages, tool_calls, results):
        messages.append({"role": "assistant", "content": [
            {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments}
            for tc in tool_calls
        ]})
        messages.append({"role": "user", "content": [
            {
                "type": "tool_result",
                "tool_use_id": r.call_id,
                "content": r.error if r.error else r.output or "",
                **({"is_error": True} if r.error else {}),
            }
            for r in results
        ]})

    def _inject_openai(self, messages, tool_calls, results):
        messages.append({
            "role": "assistant", "content": None,
            "tool_calls": [
                {
                    "id": tc.id, "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)},
                }
                for tc in tool_calls
            ]
        })
        for r in results:
            messages.append({
                "role": "tool", "tool_call_id": r.call_id,
                "content": r.error if r.error else r.output or "",
            })
