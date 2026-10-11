"""HttpTransport 单元测试:httpx.MockTransport 喂单 JSON 与 SSE 两种响应。"""

import json

import httpx
import pytest

from codepilot.mcp.rpc import RpcError
from codepilot.mcp.transport.base import McpTimeout
from codepilot.mcp.transport.http import HttpTransport


def _make_transport(handler) -> HttpTransport:
    t = HttpTransport("https://mcp.test/mcp")
    t._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://mcp.test")
    return t


def _read_request_id(request: httpx.Request) -> int:
    return json.loads(request.read())["id"]


async def test_single_json_response():
    def handler(request: httpx.Request):
        rid = _read_request_id(request)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": rid, "result": {"ok": 1}},
                              headers={"Content-Type": "application/json"})

    t = _make_transport(handler)
    try:
        assert await t.request("initialize", {}) == {"ok": 1}
    finally:
        await t.close()


async def test_sse_stream_response():
    def handler(request: httpx.Request):
        rid = _read_request_id(request)
        body = json.dumps({"jsonrpc": "2.0", "id": rid, "result": {"stream": True}})
        content = f"event: message\ndata: {body}\n\n".encode()
        return httpx.Response(200, content=content,
                              headers={"Content-Type": "text/event-stream"})

    t = _make_transport(handler)
    try:
        assert await t.request("initialize", {}) == {"stream": True}
    finally:
        await t.close()


async def test_sse_ignores_premature_ids():
    """SSE 流里先来别的 id 的帧,应被跳过,直到命中本请求 id。"""

    def handler(request: httpx.Request):
        rid = _read_request_id(request)
        a = json.dumps({"jsonrpc": "2.0", "id": 999, "result": {"wrong": True}})
        b = json.dumps({"jsonrpc": "2.0", "id": rid, "result": {"right": True}})
        content = f"data: {a}\n\ndata: {b}\n\n".encode()
        return httpx.Response(200, content=content,
                              headers={"Content-Type": "text/event-stream"})

    t = _make_transport(handler)
    try:
        assert await t.request("initialize", {}) == {"right": True}
    finally:
        await t.close()


async def test_error_envelope_raises_rpc_error():
    def handler(request: httpx.Request):
        rid = _read_request_id(request)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": rid,
                                         "error": {"code": -32601, "message": "nope"}},
                              headers={"Content-Type": "application/json"})

    t = _make_transport(handler)
    try:
        with pytest.raises(RpcError):
            await t.request("tools/call", {})
    finally:
        await t.close()


async def test_session_id_reused_on_later_request():
    seen: list[str] = []

    def handler(request: httpx.Request):
        seen.append(request.headers.get("Mcp-Session-Id", ""))
        rid = _read_request_id(request)
        return httpx.Response(200,
                              json={"jsonrpc": "2.0", "id": rid, "result": {"ok": 1}},
                              headers={"Mcp-Session-Id": "sess-abc",
                                       "Content-Type": "application/json"})

    t = _make_transport(handler)
    try:
        await t.request("initialize", {})
        await t.request("tools/list", {})
        await t.request("tools/call", {})
    finally:
        await t.close()
    # 第二次起带上会话头(第一次 initialize 可能是空的/或直接返回)
    assert "sess-abc" in seen[1:] or seen[-1] == "sess-abc"


async def test_http_timeout_raises_mcp_timeout():
    def handler(request: httpx.Request):
        raise httpx.ReadTimeout("slow", request=request)

    t = HttpTransport("https://mcp.test/mcp", timeout=0.5)
    t._client = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                  base_url="https://mcp.test", timeout=0.5)
    try:
        with pytest.raises(McpTimeout):
            await t.request("tools/call", {})
    finally:
        await t.close()