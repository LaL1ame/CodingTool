"""协议层测试 —— SSE 解析 + Delta 输出。"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mewcode.protocols.anthropic import AnthropicProtocol
from mewcode.protocols.base import Delta
from mewcode.protocols.openai import OpenAIProtocol


class _AsyncLines:
    """模拟 httpx 流式响应的 aiter_lines 返回值。"""

    def __init__(self, lines: list[str]):
        self._lines = lines
        self._idx = 0

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        if self._idx >= len(self._lines):
            raise StopAsyncIteration
        line = self._lines[self._idx]
        self._idx += 1
        return line


def _make_mock_response(lines: list[str], status_code: int = 200) -> MagicMock:
    """创建模拟 httpx 流式响应。"""
    resp = MagicMock()
    resp.status_code = status_code
    resp.aiter_lines = MagicMock(return_value=_AsyncLines(lines))
    resp.aread = AsyncMock(return_value=b"")
    return resp


def _patch_async_client(protocol_module: str, response: MagicMock):
    """用 mock client 替换 httpx.AsyncClient。"""

    class MockStreamCtx:
        def __init__(self, resp):
            self._resp = resp

        async def __aenter__(self):
            return self._resp

        async def __aexit__(self, *args):
            pass

    class MockClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def stream(self, method, url, **kwargs):
            return MockStreamCtx(response)

    return patch(f"{protocol_module}.httpx.AsyncClient", MockClient)


# ---- Anthropic 协议测试 ----------------------------------------------------

@pytest.mark.asyncio
async def test_anthropic_text_deltas():
    events = [
        json.dumps({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hello "}}),
        json.dumps({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "World"}}),
        json.dumps({"type": "message_stop"}),
    ]
    mock_resp = _make_mock_response([f"data: {e}" for e in events])

    with _patch_async_client("mewcode.protocols.anthropic", mock_resp):
        protocol = AnthropicProtocol("https://x.com", "sk-test", "claude-3")
        deltas = [d async for d in protocol.stream([{"role": "user", "content": "hi"}])]

    texts = "".join(d.text for d in deltas if d.text)
    assert texts == "Hello World"
    assert any(d.done for d in deltas)


@pytest.mark.asyncio
async def test_anthropic_thinking_filtered():
    events = [
        json.dumps({"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "Hmm..."}}),
        json.dumps({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Answer"}}),
        json.dumps({"type": "message_stop"}),
    ]
    mock_resp = _make_mock_response([f"data: {e}" for e in events])

    with _patch_async_client("mewcode.protocols.anthropic", mock_resp):
        protocol = AnthropicProtocol("https://x.com", "sk-test", "claude-3")
        deltas = [d async for d in protocol.stream([{"role": "user", "content": "hi"}])]

    thinking_deltas = [d for d in deltas if d.thinking]
    assert len(thinking_deltas) == 1
    assert thinking_deltas[0].thinking == "Hmm..."

    text_deltas = [d for d in deltas if d.text]
    assert len(text_deltas) == 1
    assert text_deltas[0].text == "Answer"


# ---- OpenAI 协议测试 --------------------------------------------------------

@pytest.mark.asyncio
async def test_openai_text_deltas():
    events = [
        json.dumps({"choices": [{"delta": {"content": "Hello"}}]}),
        json.dumps({"choices": [{"delta": {"content": " "}}]}),
        json.dumps({"choices": [{"delta": {"content": "World"}}]}),
        "[DONE]",
    ]
    mock_resp = _make_mock_response([f"data: {e}" for e in events])

    with _patch_async_client("mewcode.protocols.openai", mock_resp):
        protocol = OpenAIProtocol("https://x.com", "sk-test", "gpt-4o")
        deltas = [d async for d in protocol.stream([{"role": "user", "content": "hi"}])]

    texts = "".join(d.text for d in deltas if d.text)
    assert texts == "Hello World"
    assert deltas[-1].done


@pytest.mark.asyncio
async def test_openai_error():
    mock_resp = _make_mock_response([], status_code=401)
    mock_resp.aread = AsyncMock(return_value=b"Unauthorized")

    with _patch_async_client("mewcode.protocols.openai", mock_resp):
        protocol = OpenAIProtocol("https://x.com", "sk-bad", "gpt-4o")
        deltas = [d async for d in protocol.stream([{"role": "user", "content": "hi"}])]

    assert len(deltas) == 1
    assert deltas[0].error is not None
    assert "401" in deltas[0].error


# ---- Delta 数据类 ----------------------------------------------------------

def test_delta_defaults() -> None:
    d = Delta()
    assert d.text is None
    assert d.thinking is None
    assert d.done is False
    assert d.error is None


def test_delta_text() -> None:
    d = Delta(text="hello")
    assert d.text == "hello"


def test_delta_done() -> None:
    d = Delta(done=True)
    assert d.done is True
