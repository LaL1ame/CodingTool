"""协议层：Delta + BaseProtocol + AnthropicProtocol + OpenAIProtocol。"""

import json
import re
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx

from codepilot.prompt import SystemPrompt


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
    """单轮 LLM 调用的 Token 用量（Anthropic 含缓存命中信息）。"""
    input_tokens: int
    output_tokens: int
    cache_read: int | None = None          # 命中缓存的 token（Anthropic）
    cache_creation: int | None = None      # 写入缓存的 token（Anthropic）


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
    # —— state10:目标暂停(可续接) / 空转提示 ——
    paused: bool = False            # F2:达上限或持续空转,目标暂停(可续接,非 error)
    pause_reason: str | None = None  # "limit" | "stall" | "unknown_tool"
    stall_hint: str | None = None    # F3:空转软提示(非 None 即命中;此时仍在跑,非停)
    resume_round: int | None = None  # F2:已达轮数(供"/continue 续跑"提示)


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

    @abstractmethod
    async def complete(self, system: str, messages: list[dict]) -> str:
        """非流式纯文本完成(F4.3 载体):给摘要这类独立、禁工具、不入主对话
        统计的调用。返回完整文本;失败抛 HTTPError。usage 不回流主 Delta。
        """
        ...


class AnthropicProtocol(BaseProtocol):
    async def stream(
        self, messages: list[dict], thinking: bool = False,
        tools: list[dict] | None = None,
        system: SystemPrompt | None = None,
    ) -> AsyncIterator[Delta]:
        body: dict = {"model": self.model, "messages": messages, "stream": True, "max_tokens": 4096}
        if thinking:
            body["thinking"] = {"type": "enabled", "budget_tokens": 2000}
        if system:
            body["system"] = _serialize_system(system)
        if tools:
            body["tools"] = tools
            _mark_tools_cacheable(tools)  # 工具定义进缓存

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

    async def complete(self, system: str, messages: list[dict]) -> str:
        """Anthropic 非流式纯文本完成(禁工具——请求体不含 tools 字段)。"""
        body: dict = {
            "model": self.model, "messages": messages,
            "max_tokens": 4096, "system": [{"type": "text", "text": system}],
        }
        headers = {
            "x-api-key": self.api_key, "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        url = f"{self.base_url}/v1/messages"
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(url, json=body, headers=headers)
            if resp.status_code != 200:
                raise httpx.HTTPError(f"HTTP {resp.status_code}: {resp.text[:300]}")
            data = resp.json()
            parts = [b.get("text", "") for b in data.get("content", [])
                     if isinstance(b, dict) and b.get("type") == "text"]
            return "".join(parts)

    async def _stream_from_lines(self, lines: AsyncIterator[str]) -> AsyncIterator[Delta]:
        """把 SSE 文本行流解析为 Delta 事件（与 I/O 解耦，便于测试）。

        usage 累积三处：
        - `message_start` 的 `message.usage`：input_tokens / cache_read / cache_creation
          （DeepSeek 兼容端点在此给出 input/cache）；
        - `message_delta` 的 `usage`：output_tokens（DeepSeek 在此给出 output）；
        - `message_stop` 的 `usage`：旧路径兜底（部分端点在此给全量）。
        三者合并，避免 token/cache 计数静默为 0（state17 已知问题，2026-10 修）。
        """
        tool_blocks: dict[int, dict] = {}
        acc: dict[str, int | None | bool] = {
            "input_tokens": None, "output_tokens": None,
            "cache_read": None, "cache_creation": None,
        }

        def _merge(src: dict) -> None:
            for key, ak in (
                ("input_tokens", "input_tokens"),
                ("output_tokens", "output_tokens"),
                ("cache_read_input_tokens", "cache_read"),
                ("cache_creation_input_tokens", "cache_creation"),
            ):
                v = src.get(key)
                if isinstance(v, int):
                    acc[ak] = v

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

                elif t == "message_start":
                    # DeepSeek 兼容端点在 message_start 给出 input/cache
                    msg = ev.get("message", {})
                    if isinstance(msg, dict):
                        _merge(msg.get("usage") or {})

                elif t == "message_delta":
                    # DeepSeek 兼容端点在 message_delta 给出 output
                    u = ev.get("usage")
                    if isinstance(u, dict):
                        _merge(u)

                elif t == "message_stop":
                    # 旧路径兜底：部分端点在 message_stop 给全量 usage
                    stop_usage = ev.get("usage")
                    if isinstance(stop_usage, dict):
                        _merge(stop_usage)
                    if (acc["input_tokens"] is not None
                            or acc["output_tokens"] is not None):
                        yield Delta(usage=Usage(
                            input_tokens=acc["input_tokens"] or 0,
                            output_tokens=acc["output_tokens"] or 0,
                            cache_read=acc["cache_read"],
                            cache_creation=acc["cache_creation"],
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


def _mark_tools_cacheable(tools: list[dict]) -> None:
    """给 tools 数组末位挂 cache_control，使工具定义进入缓存前缀。"""
    if tools:
        tools[-1]["cache_control"] = {"type": "ephemeral"}


def _serialize_system(system: SystemPrompt) -> list[dict]:
    """把分层 SystemPrompt 序列化为 Anthropic system 块列表。

    稳定前缀块逐块写入，末位 stable 挂 cache_control 断点；volatile 尾块追加在断点后、
    不挂 cache_control — 保证前缀进缓存、动态内容不进。
    """
    blocks: list[dict] = []
    for i, text in enumerate(system.stable):
        block: dict = {"type": "text", "text": text}
        if i == len(system.stable) - 1:
            block["cache_control"] = {"type": "ephemeral"}
        blocks.append(block)
    for text in system.volatile:
        blocks.append({"type": "text", "text": text})
    return blocks


def _chat_completions_url(base_url: str) -> str:
    """推导 chat/completions 端点。

    base_url 已含版本段（如 .../v3、.../v1）时直接在其后接 /chat/completions；
    否则（裸 origin，如 https://api.deepseek.com）补齐 /v1/chat/completions。
    兼容 OpenAI 官方 base（版本段自带）与裸 origin 两种写法。
    """
    base = base_url.rstrip("/")
    if re.search(r"/v\d+(beta)?$", base):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


class OpenAIProtocol(BaseProtocol):
    async def stream(
        self, messages: list[dict], thinking: bool = False,
        tools: list[dict] | None = None,
        system: SystemPrompt | None = None,
    ) -> AsyncIterator[Delta]:
        _ = thinking
        if system:
            merged = "\n\n".join(system.stable + system.volatile)
            messages = [{"role": "system", "content": merged}, *messages]
        body: dict = {"model": self.model, "messages": messages, "stream": True,
                      "stream_options": {"include_usage": True}}
        if tools:
            body["tools"] = tools

        headers = {"Authorization": f"Bearer {self.api_key}", "content-type": "application/json"}
        url = _chat_completions_url(self.base_url)

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

    async def complete(self, system: str, messages: list[dict]) -> str:
        """OpenAI 非流式纯文本完成(禁工具——不传 tools 字段)。"""
        body: dict = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "stream": False,
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "content-type": "application/json"}
        url = _chat_completions_url(self.base_url)
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(url, json=body, headers=headers)
            if resp.status_code != 200:
                raise httpx.HTTPError(f"HTTP {resp.status_code}: {resp.text[:300]}")
            data = resp.json()
            return data["choices"][0]["message"]["content"] or ""

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
