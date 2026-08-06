"""测试协议层 — Anthropic/OpenAI 工具调用 SSE 解析。"""

import json

import pytest

from mewcode.protocols import AnthropicProtocol, OpenAIProtocol, Delta, ToolCall


# ── SSE 模拟工具 ──────────────────────────────────────────────

def _make_anthropic_sse(lines: list[str]) -> list[str]:
    return [f"data: {line}" for line in lines if line and not line.startswith(":")]


def _make_openai_sse(chunks: list[dict]) -> list[str]:
    result = [f"data: {json.dumps(c)}" for c in chunks]
    result.append("data: [DONE]")
    return result


# ── Monkey-patch _stream_from_lines ───────────────────────────

async def _stream_from_lines_anthropic(self, lines):
    tool_blocks: dict[int, dict] = {}
    for line in lines:
        if not line or not line.startswith("data: "):
            continue
        try:
            ev = json.loads(line[6:])
        except json.JSONDecodeError:
            continue
        t = ev.get("type", "")
        if t == "content_block_start":
            block = ev.get("content_block", {})
            if block.get("type") == "tool_use":
                idx = ev.get("index", 0)
                tool_blocks[idx] = {"id": block.get("id", ""), "name": block.get("name", ""), "args_json": ""}
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


async def _stream_from_lines_openai(self, lines):
    tool_calls_map: dict[int, dict] = {}
    tool_calls_produced = False
    for line in lines:
        if not line or not line.startswith("data: "):
            continue
        data = line[6:]
        if data.strip() == "[DONE]":
            if not tool_calls_produced:
                yield Delta(done=True)
            continue
        try:
            ev = json.loads(data)
        except json.JSONDecodeError:
            continue
        choices = ev.get("choices", [])
        if not choices:
            continue
        choice = choices[0]
        delta = choice.get("delta", {})
        finish_reason = choice.get("finish_reason", "")
        content = delta.get("content", "")
        if content:
            yield Delta(text=content)
        for tc in delta.get("tool_calls", []):
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
                tc_ = tool_calls_map[idx]
                try:
                    args = json.loads(tc_["args_json"]) if tc_["args_json"] else {}
                except json.JSONDecodeError:
                    args = {}
                calls.append(ToolCall(id=tc_["id"], name=tc_["name"], arguments=args))
            yield Delta(tool_calls=calls)
            tool_calls_produced = True
        elif finish_reason == "stop":
            yield Delta(done=True)


AnthropicProtocol._stream_from_lines = _stream_from_lines_anthropic
OpenAIProtocol._stream_from_lines = _stream_from_lines_openai


# ── Tests ─────────────────────────────────────────────────────

class TestAnthropicToolUse:
    async def test_single_tool_use(self):
        events = _make_anthropic_sse([
            '{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}',
            '{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Let me read."}}',
            '{"type":"content_block_stop","index":0}',
            '{"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"t1","name":"read"}}',
            '{"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{\\"file_path\\":\\""}}',
            '{"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"app.py\\"}"}}',
            '{"type":"content_block_stop","index":1}',
            '{"type":"message_stop"}',
        ])
        proto = AnthropicProtocol("http://m", "k", "m")
        deltas = [d async for d in proto._stream_from_lines(events)]
        tools = [d for d in deltas if d.tool_calls]
        assert len(tools) == 1 and len(tools[0].tool_calls) == 1
        tc = tools[0].tool_calls[0]
        assert tc.name == "read" and tc.arguments == {"file_path": "app.py"}

    async def test_multiple_tool_uses(self):
        events = _make_anthropic_sse([
            '{"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"t1","name":"read"}}',
            '{"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\\"file_path\\":\\"a.py\\"}"}}',
            '{"type":"content_block_stop","index":0}',
            '{"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"t2","name":"grep"}}',
            '{"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{\\"pattern\\":\\"TODO\\"}"}}',
            '{"type":"content_block_stop","index":1}',
            '{"type":"message_stop"}',
        ])
        proto = AnthropicProtocol("http://m", "k", "m")
        deltas = [d async for d in proto._stream_from_lines(events)]
        tools = [d for d in deltas if d.tool_calls]
        assert len(tools[0].tool_calls) == 2

    async def test_pure_text_no_tool_calls(self):
        events = _make_anthropic_sse([
            '{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}',
            '{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hi"}}',
            '{"type":"content_block_stop","index":0}',
            '{"type":"message_stop"}',
        ])
        proto = AnthropicProtocol("http://m", "k", "m")
        deltas = [d async for d in proto._stream_from_lines(events)]
        assert any(d.done for d in deltas)
        assert not any(d.tool_calls for d in deltas)


class TestOpenAIToolCalls:
    async def test_single_tool_call(self):
        chunks = [
            {"choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "read"}}]}, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"file_path":"app.py"}'}}]}, "finish_reason": "tool_calls"}]},
        ]
        proto = OpenAIProtocol("http://m", "k", "m")
        deltas = [d async for d in proto._stream_from_lines(_make_openai_sse(chunks))]
        tools = [d for d in deltas if d.tool_calls]
        assert len(tools) == 1 and tools[0].tool_calls[0].name == "read"
        assert not any(d.done for d in deltas)

    async def test_multiple_tool_calls(self):
        chunks = [
            {"choices": [{"index": 0, "delta": {"tool_calls": [
                {"index": 0, "id": "c1", "function": {"name": "read"}},
                {"index": 1, "id": "c2", "function": {"name": "grep"}},
            ]}, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": '{"file_path":"a.py"}'}},
                {"index": 1, "function": {"arguments": '{"pattern":"TODO"}'}},
            ]}, "finish_reason": "tool_calls"}]},
        ]
        proto = OpenAIProtocol("http://m", "k", "m")
        deltas = [d async for d in proto._stream_from_lines(_make_openai_sse(chunks))]
        tools = [d for d in deltas if d.tool_calls]
        assert len(tools[0].tool_calls) == 2

    async def test_pure_text(self):
        chunks = [
            {"choices": [{"index": 0, "delta": {"content": "Hi"}, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        ]
        proto = OpenAIProtocol("http://m", "k", "m")
        deltas = [d async for d in proto._stream_from_lines(_make_openai_sse(chunks))]
        assert any(d.done for d in deltas)
        assert any(d.text for d in deltas)
