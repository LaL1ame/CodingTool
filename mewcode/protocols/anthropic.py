"""Anthropic Messages API 协议适配 — SSE 事件解析 + thinking 过滤。"""

import json
from collections.abc import AsyncIterator

import httpx

from mewcode.protocols.base import BaseProtocol, Delta

ANTHROPIC_VERSION = "2023-06-01"


class AnthropicProtocol(BaseProtocol):
    """封装 Anthropic Messages API 的流式请求与响应解析。"""

    async def stream(
        self, messages: list[dict], thinking: bool = False
    ) -> AsyncIterator[Delta]:
        body: dict = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "max_tokens": 4096,
        }
        if thinking:
            body["thinking"] = {"type": "enabled", "budget_tokens": 2000}

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        url = f"{self.base_url}/v1/messages"

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream(
                    "POST", url, json=body, headers=headers
                ) as response:
                    if response.status_code != 200:
                        error_text = await response.aread()
                        yield Delta(error=f"HTTP {response.status_code}: {error_text.decode(errors='replace')[:500]}")
                        return

                    async for line in response.aiter_lines():
                        if not line or line.startswith(":"):
                            continue
                        if line.startswith("data: "):
                            data_str = line[len("data: "):]
                            try:
                                event = json.loads(data_str)
                            except json.JSONDecodeError:
                                continue

                            event_type = event.get("type", "")

                            if event_type == "content_block_delta":
                                delta = event.get("delta", {})
                                delta_type = delta.get("type", "")
                                if delta_type == "text_delta":
                                    yield Delta(text=delta.get("text", ""))
                                elif delta_type == "thinking_delta":
                                    yield Delta(thinking=delta.get("thinking", ""))
                                elif delta_type == "input_json_delta":
                                    pass  # 本期不处理 tool use

                            elif event_type == "message_stop":
                                yield Delta(done=True)

                            elif event_type == "error":
                                err = event.get("error", {})
                                yield Delta(error=err.get("message", str(err)))

        except httpx.HTTPError as e:
            yield Delta(error=str(e))
