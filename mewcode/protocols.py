"""协议层：Delta + BaseProtocol + AnthropicProtocol + OpenAIProtocol。"""

import json
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx


@dataclass
class Delta:
    text: str | None = None
    thinking: str | None = None
    done: bool = False
    error: str | None = None


class BaseProtocol(ABC):
    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    @abstractmethod
    async def stream(self, messages: list[dict], thinking: bool = False) -> AsyncIterator[Delta]:
        ...


class AnthropicProtocol(BaseProtocol):
    async def stream(self, messages: list[dict], thinking: bool = False) -> AsyncIterator[Delta]:
        body: dict = {"model": self.model, "messages": messages, "stream": True, "max_tokens": 4096}
        if thinking:
            body["thinking"] = {"type": "enabled", "budget_tokens": 2000}
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        url = f"{self.base_url}/v1/messages"
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream("POST", url, json=body, headers=headers) as resp:
                    if resp.status_code != 200:
                        err = await resp.aread()
                        yield Delta(error=f"HTTP {resp.status_code}: {err.decode(errors='replace')[:500]}")
                        return
                    async for line in resp.aiter_lines():
                        if not line or line.startswith(":"):
                            continue
                        if line.startswith("data: "):
                            try:
                                ev = json.loads(line[6:])
                            except json.JSONDecodeError:
                                continue
                            t = ev.get("type", "")
                            if t == "content_block_delta":
                                d = ev.get("delta", {})
                                dt = d.get("type", "")
                                if dt == "text_delta":
                                    yield Delta(text=d.get("text", ""))
                                elif dt == "thinking_delta":
                                    yield Delta(thinking=d.get("thinking", ""))
                            elif t == "message_stop":
                                yield Delta(done=True)
                            elif t == "error":
                                err = ev.get("error", {})
                                yield Delta(error=err.get("message", str(err)))
        except httpx.HTTPError as e:
            yield Delta(error=str(e))


class OpenAIProtocol(BaseProtocol):
    async def stream(self, messages: list[dict], thinking: bool = False) -> AsyncIterator[Delta]:
        _ = thinking
        body: dict = {"model": self.model, "messages": messages, "stream": True}
        headers = {"Authorization": f"Bearer {self.api_key}", "content-type": "application/json"}
        url = f"{self.base_url}/v1/chat/completions"
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream("POST", url, json=body, headers=headers) as resp:
                    if resp.status_code != 200:
                        err = await resp.aread()
                        yield Delta(error=f"HTTP {resp.status_code}: {err.decode(errors='replace')[:500]}")
                        return
                    async for line in resp.aiter_lines():
                        if not line or line.startswith(":"):
                            continue
                        if line.startswith("data: "):
                            data = line[6:]
                            if data.strip() == "[DONE]":
                                yield Delta(done=True)
                                continue
                            try:
                                ev = json.loads(data)
                            except json.JSONDecodeError:
                                continue
                            choices = ev.get("choices", [])
                            if choices:
                                d = choices[0].get("delta", {})
                                c = d.get("content", "")
                                if c:
                                    yield Delta(text=c)
        except httpx.HTTPError as e:
            yield Delta(error=str(e))
