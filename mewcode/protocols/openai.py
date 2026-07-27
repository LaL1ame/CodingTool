"""OpenAI Chat Completions API 协议适配 — SSE 事件解析。"""

import json
from collections.abc import AsyncIterator

import httpx

from mewcode.protocols.base import BaseProtocol, Delta


class OpenAIProtocol(BaseProtocol):
    """封装 OpenAI Chat Completions API 的流式请求与响应解析。"""

    async def stream(
        self, messages: list[dict], thinking: bool = False
    ) -> AsyncIterator[Delta]:
        # OpenAI 协议无 native thinking 参数，忽略 thinking 标志
        _ = thinking

        body: dict = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "content-type": "application/json",
        }
        url = f"{self.base_url}/v1/chat/completions"

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
                            if data_str.strip() == "[DONE]":
                                yield Delta(done=True)
                                continue
                            try:
                                event = json.loads(data_str)
                            except json.JSONDecodeError:
                                continue

                            choices = event.get("choices", [])
                            if choices:
                                delta = choices[0].get("delta", {})
                                content = delta.get("content", "")
                                if content:
                                    yield Delta(text=content)

        except httpx.HTTPError as e:
            yield Delta(error=str(e))
