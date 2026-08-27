"""协议层：Delta + BaseProtocol + AnthropicProtocol + OpenAIProtocol。"""

import json
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx


@dataclass
class ToolCall:
    """统一的工具调用内部表示 — 跨协议。"""
    id: str
    name: str
    arguments: dict


@dataclass
class ToolResult:
    """工具执行结果 — 成功或失败的结构化回传。"""
    call_id: str
    name: str
    output: str | None = None
    error: str | None = None
    truncated: bool = False
    total_items: int | None = None


@dataclass
class Usage:
    """单轮 LLM 调用的 Token 用量。"""
    input_tokens: int
    output_tokens: int


@dataclass
class Delta:
    text: str | None = None
    thinking: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_result: ToolResult | None = None
    usage: Usage | None = None
    round: int | None = None
    max_rounds: int | None = None
    cancelled: bool = False
    done: bool = False
    error: str | None = None


class BaseProtocol(ABC):
    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    @abstractmethod
    async def stream(
        self, messages: list[dict], thinking: bool = False,
        tools: list[dict] | None = None,
    ) -> AsyncIterator[Delta]:
        ...


class AnthropicProtocol(BaseProtocol):
    async def stream(
        self, messages: list[dict], thinking: bool = False,
        tools: list[dict] | None = None,
    ) -> AsyncIterator[Delta]:
        body: dict = {"model": self.model, "messages": messages, "stream": True, "max_tokens": 4096}
        if thinking:
            body["thinking"] = {"type": "enabled", "budget_tokens": 2000}
        if tools:
            body["tools"] = tools

        headers = {
            "x-api-key": self.api_key, "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        url = f"{self.base_url}/v1/messages"

        try:
            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream("POST", url, json=body, headers=headers) as resp:
                    if resp.status_code != 200:
                        err = await resp.aread()
                        yield Delta(error=f"HTTP {resp.status_code}: {err.decode(errors='replace')[:500]}")
                        return
                    async for d in self._stream_from_lines(resp.aiter_lines()):
                        yield d
        except httpx.HTTPError as e:
            yield Delta(error=str(e))

    async def _stream_from_lines(self, lines: AsyncIterator[str]) -> AsyncIterator[Delta]:
        """把 SSE 文本行流解析为 Delta 事件（与 I/O 解耦，便于测试）。"""
        tool_blocks: dict[int, dict] = {}
        async for line in lines:
            if not line or line.startswith(":"):
                continue
            if line.startswith("data: "):
                try:
                    ev = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                t = ev.get("type", "")

                if t == "content_block_start":
                    block = ev.get("content_block", {})
                    if block.get("type") == "tool_use":
                        idx = ev.get("index", 0)
                        tool_blocks[idx] = {
                            "id": block.get("id", ""),
                            "name": block.get("name", ""),
                            "args_json": "",
                        }

                elif t == "content_block_delta":
                    d = ev.get("delta", {})
                    dt = d.get("type", "")
                    if dt == "text_delta":
                        yield Delta(text=d.get("text", ""))
                    elif dt == "thinking_delta":
                        yield Delta(thinking=d.get("thinking", ""))
                    elif dt == "input_json_delta":
                        idx = ev.get("index", 0)
                        if idx in tool_blocks:
                            tool_blocks[idx]["args_json"] += d.get("partial_json", "")

                elif t == "content_block_stop":
                    pass

                elif t == "message_stop":
                    usage = ev.get("usage")
                    if usage:
                        yield Delta(usage=Usage(
                            input_tokens=usage.get("input_tokens", 0),
                            output_tokens=usage.get("output_tokens", 0),
                        ))
                    if tool_blocks:
                        calls = []
                        for idx in sorted(tool_blocks.keys()):
                            tb = tool_blocks[idx]
                            try:
                                args = json.loads(tb["args_json"]) if tb["args_json"] else {}
                            except json.JSONDecodeError:
                                args = {}
                            calls.append(ToolCall(id=tb["id"], name=tb["name"], arguments=args))
                        yield Delta(tool_calls=calls)
                    else:
                        yield Delta(done=True)

                elif t == "error":
                    err = ev.get("error", {})
                    yield Delta(error=err.get("message", str(err)))


class OpenAIProtocol(BaseProtocol):
    async def stream(
        self, messages: list[dict], thinking: bool = False,
        tools: list[dict] | None = None,
    ) -> AsyncIterator[Delta]:
        _ = thinking
        body: dict = {"model": self.model, "messages": messages, "stream": True,
                      "stream_options": {"include_usage": True}}
        if tools:
            body["tools"] = tools

        headers = {"Authorization": f"Bearer {self.api_key}", "content-type": "application/json"}
        url = f"{self.base_url}/v1/chat/completions"

        try:
            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream("POST", url, json=body, headers=headers) as resp:
                    if resp.status_code != 200:
                        err = await resp.aread()
                        yield Delta(error=f"HTTP {resp.status_code}: {err.decode(errors='replace')[:500]}")
                        return
                    async for d in self._stream_from_lines(resp.aiter_lines()):
                        yield d
        except httpx.HTTPError as e:
            yield Delta(error=str(e))

    async def _stream_from_lines(self, lines: AsyncIterator[str]) -> AsyncIterator[Delta]:
        """把 SSE 文本行流解析为 Delta 事件（与 I/O 解耦，便于测试）。"""
        tool_calls_map: dict[int, dict] = {}
        tool_calls_produced = False

        async for line in lines:
            if not line or line.startswith(":"):
                continue
            if line.startswith("data: "):
                data = line[6:]
                if data.strip() == "[DONE]":
                    if not tool_calls_produced:
                        yield Delta(done=True)
                    continue
                try:
                    ev = json.loads(data)
                except json.JSONDecodeError:
                    continue

                usage = ev.get("usage")
                if usage:
                    yield Delta(usage=Usage(
                        input_tokens=usage.get("prompt_tokens", 0),
                        output_tokens=usage.get("completion_tokens", 0),
                    ))

                choices = ev.get("choices", [])
                if not choices:
                    continue
                choice = choices[0]
                delta = choice.get("delta", {})
                finish_reason = choice.get("finish_reason", "")

                content = delta.get("content", "")
                if content:
                    yield Delta(text=content)

                tc_deltas = delta.get("tool_calls", [])
                for tc in tc_deltas:
                    idx = tc.get("index", 0)
                    if idx not in tool_calls_map:
                        tool_calls_map[idx] = {"id": "", "name": "", "args_json": ""}
                    entry = tool_calls_map[idx]
                    if "id" in tc and tc["id"]:
                        entry["id"] = tc["id"]
                    func = tc.get("function", {})
                    if "name" in func and func["name"]:
                        entry["name"] = func["name"]
                    if "arguments" in func:
                        entry["args_json"] += func["arguments"]

                if finish_reason == "tool_calls":
                    calls = []
                    for idx in sorted(tool_calls_map.keys()):
                        tc = tool_calls_map[idx]
                        try:
                            args = json.loads(tc["args_json"]) if tc["args_json"] else {}
                        except json.JSONDecodeError:
                            args = {}
                        calls.append(ToolCall(id=tc["id"], name=tc["name"], arguments=args))
                    yield Delta(tool_calls=calls)
                    tool_calls_produced = True

                elif finish_reason == "stop":
                    yield Delta(done=True)
