"""测试协议层 — Anthropic/OpenAI 流式解析（含 Token 用量）。"""

import json

from codepilot.prompt import SystemPrompt
from codepilot.protocols import (
    AnthropicProtocol, OpenAIProtocol, _chat_completions_url,
    _mark_tools_cacheable, _serialize_system,
)


def _sp(stable=("S1", "S2", "S3"), volatile=("v1", "v2")):
    return SystemPrompt(stable=list(stable), volatile=list(volatile))


# ── SSE 模拟工具 ──────────────────────────────────────────────

def _make_anthropic_sse(lines: list[str]) -> list[str]:
    return [f"data: {line}" for line in lines if line and not line.startswith(":")]


def _make_openai_sse(chunks: list[dict]) -> list[str]:
    result = [f"data: {json.dumps(c)}" for c in chunks]
    result.append("data: [DONE]")
    return result


async def _async_lines(lines: list[str]):
    for line in lines:
        yield line


async def _anthropic(lines: list[str]):
    proto = AnthropicProtocol("http://m", "k", "m")
    events = _make_anthropic_sse(lines)
    deltas = [d async for d in proto._stream_from_lines(_async_lines(events))]
    return proto, deltas


async def _openai(chunks: list[dict]):
    proto = OpenAIProtocol("http://m", "k", "m")
    events = _make_openai_sse(chunks)
    deltas = [d async for d in proto._stream_from_lines(_async_lines(events))]
    return proto, deltas


# ── Tests ─────────────────────────────────────────────────────

class TestAnthropicToolUse:
    async def test_single_tool_use(self):
        _, deltas = await _anthropic([
            '{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}',
            '{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Let me read."}}',
            '{"type":"content_block_stop","index":0}',
            '{"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"t1","name":"read"}}',
            '{"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{\\"file_path\\":\\""}}',
            '{"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"app.py\\"}"}}',
            '{"type":"content_block_stop","index":1}',
            '{"type":"message_stop"}',
        ])
        tools = [d for d in deltas if d.tool_calls]
        assert len(tools) == 1 and len(tools[0].tool_calls) == 1
        tc = tools[0].tool_calls[0]
        assert tc.name == "read" and tc.arguments == {"file_path": "app.py"}

    async def test_multiple_tool_uses(self):
        _, deltas = await _anthropic([
            '{"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"t1","name":"read"}}',
            '{"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\\"file_path\\":\\"a.py\\"}"}}',
            '{"type":"content_block_stop","index":0}',
            '{"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"t2","name":"grep"}}',
            '{"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{\\"pattern\\":\\"TODO\\"}"}}',
            '{"type":"content_block_stop","index":1}',
            '{"type":"message_stop"}',
        ])
        tools = [d for d in deltas if d.tool_calls]
        assert len(tools[0].tool_calls) == 2

    async def test_pure_text_no_tool_calls(self):
        _, deltas = await _anthropic([
            '{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}',
            '{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hi"}}',
            '{"type":"content_block_stop","index":0}',
            '{"type":"message_stop"}',
        ])
        assert any(d.done for d in deltas)
        assert not any(d.tool_calls for d in deltas)

    async def test_usage_event(self):
        _, deltas = await _anthropic([
            '{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}',
            '{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hi"}}',
            '{"type":"content_block_stop","index":0}',
            '{"type":"message_stop","usage":{"input_tokens":42,"output_tokens":7}}',
        ])
        usages = [d for d in deltas if d.usage]
        assert len(usages) == 1
        assert usages[0].usage.input_tokens == 42
        assert usages[0].usage.output_tokens == 7


class TestOpenAIToolCalls:
    async def test_single_tool_call(self):
        _, deltas = await _openai([
            {"choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "read"}}]}, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"file_path":"app.py"}'}}]}, "finish_reason": "tool_calls"}]},
        ])
        tools = [d for d in deltas if d.tool_calls]
        assert len(tools) == 1 and tools[0].tool_calls[0].name == "read"
        assert not any(d.done for d in deltas)

    async def test_multiple_tool_calls(self):
        _, deltas = await _openai([
            {"choices": [{"index": 0, "delta": {"tool_calls": [
                {"index": 0, "id": "c1", "function": {"name": "read"}},
                {"index": 1, "id": "c2", "function": {"name": "grep"}},
            ]}, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": '{"file_path":"a.py"}'}},
                {"index": 1, "function": {"arguments": '{"pattern":"TODO"}'}},
            ]}, "finish_reason": "tool_calls"}]},
        ])
        tools = [d for d in deltas if d.tool_calls]
        assert len(tools[0].tool_calls) == 2

    async def test_pure_text(self):
        _, deltas = await _openai([
            {"choices": [{"index": 0, "delta": {"content": "Hi"}, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        ])
        assert any(d.done for d in deltas)
        assert any(d.text for d in deltas)

    async def test_usage_event(self):
        _, deltas = await _openai([
            {"choices": [{"index": 0, "delta": {"content": "Hi"}, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"prompt_tokens": 42, "completion_tokens": 7, "total_tokens": 49}},
        ])
        usages = [d for d in deltas if d.usage]
        assert len(usages) == 1
        assert usages[0].usage.input_tokens == 42
        assert usages[0].usage.output_tokens == 7


# ── chat/completions URL 推导 ─────────────────────────────────

def test_url_bare_origin_appends_v1():
    assert _chat_completions_url("https://api.deepseek.com") == \
        "https://api.deepseek.com/v1/chat/completions"


def test_url_with_version_does_not_duplicate():
    assert _chat_completions_url("https://ark.cn-beijing.volces.com/api/coding/v3") == \
        "https://ark.cn-beijing.volces.com/api/coding/v3/chat/completions"


def test_url_with_v1_version():
    assert _chat_completions_url("https://x.example.com/v1") == \
        "https://x.example.com/v1/chat/completions"


def test_url_strips_trailing_slash():
    assert _chat_completions_url("https://api.deepseek.com/") == \
        "https://api.deepseek.com/v1/chat/completions"


# ── SystemPrompt 序列化（Anthropic 缓存断点）──────────────────

def test_serialize_system_breakpoint_on_last_stable():
    blocks = _serialize_system(_sp(["S1", "S2", "S3"], ["v1", "v2"]))
    # 3 个 stable 块 + 2 个 volatile 块 = 5 块
    assert [b["text"] for b in blocks] == ["S1", "S2", "S3", "v1", "v2"]
    # cache_control 只在最后一块 stable 上
    assert blocks[0].get("cache_control") is None
    assert blocks[1].get("cache_control") is None
    assert blocks[2]["cache_control"] == {"type": "ephemeral"}
    # volatile 尾块一律不挂
    assert blocks[3].get("cache_control") is None
    assert blocks[4].get("cache_control") is None


def test_serialize_system_single_stable_still_has_breakpoint():
    blocks = _serialize_system(_sp(["only"], []))
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}


def test_mark_tools_cacheable_last_only():
    tools = [{"name": "read"}, {"name": "write"}, {"name": "edit"}]
    _mark_tools_cacheable(tools)
    assert tools[0].get("cache_control") is None
    assert tools[1].get("cache_control") is None
    assert tools[2]["cache_control"] == {"type": "ephemeral"}


def test_mark_tools_cacheable_empty_noop():
    _mark_tools_cacheable([])  # 不应抛异常


class TestCacheFieldParsing:
    async def test_anthropic_cache_fields(self):
        _, deltas = await _anthropic([
            '{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}',
            '{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hi"}}',
            '{"type":"content_block_stop","index":0}',
            '{"type":"message_stop","usage":{"input_tokens":10,"output_tokens":3,'
            '"cache_read_input_tokens":55,"cache_creation_input_tokens":20}}',
        ])
        usages = [d for d in deltas if d.usage]
        assert len(usages) == 1
        assert usages[0].usage.cache_read == 55
        assert usages[0].usage.cache_creation == 20
