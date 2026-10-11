"""Streamable HTTP 传输:连接远程 MCP server。

MCP Streamable HTTP 的响应形态服务端实现不一:
- 可能直接回单条 JSON-RPC 响应(application/json);
- 也可能回 SSE 流(text/event-stream),事件里逐步携带最终那条 JSON-RPC 响应。

本传输同时处理这两种;并把服务端返回的 `Mcp-Session-Id` 缓存在后续请求头里透传。
请求统一带 `MCP-Protocol-Version` 头;通知(无 id)与请求(id)都走 POST,无线程复用问题。
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from codepilot.mcp.rpc import build_notification, build_request, parse_response
from codepilot.mcp.transport.base import McpTimeout, Transport

logger = __import__("logging").getLogger("codepilot.mcp.http")


class HttpTransport(Transport):
    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        *,
        timeout: float = 30.0,
        protocol_version: str = "2024-11-05",
    ) -> None:
        self._url = url
        self._base_headers = dict(headers or {})
        self._protocol_version = protocol_version
        self._timeout = timeout
        self._session_id: str | None = None
        self._client: httpx.AsyncClient | None = None
        self._next_id = 1

    async def connect(self) -> None:
        self._client = httpx.AsyncClient(timeout=self._timeout)

    def _headers(self) -> dict[str, str]:
        h = dict(self._base_headers)
        h.setdefault("Content-Type", "application/json")
        h.setdefault("Accept", "application/json, text/event-stream")
        h["MCP-Protocol-Version"] = self._protocol_version
        if self._session_id:
            h["Mcp-Session-Id"] = self._session_id
        return h

    def _next_request_id(self) -> int:
        req_id = self._next_id
        self._next_id += 1
        return req_id

    async def request(self, method: str, params: dict | None = None) -> Any:
        if self._client is None:
            raise RuntimeError("http transport 未 connect")
        req_id = self._next_request_id()
        request_msg = build_request(req_id, method, params)
        try:
            resp = await self._client.post(self._url, json=request_msg, headers=self._headers())
        except httpx.TimeoutException:
            raise McpTimeout(method, self._timeout) from None

        sid = resp.headers.get("mcp-session-id")
        if sid:
            self._session_id = sid

        if "text/event-stream" in resp.headers.get("content-type", ""):
            output = await self._collect_sse_response(resp, req_id)
        else:
            output = resp.json()

        if len(output) == 0:
            raise McpTimeout(method, self._timeout)
        return parse_response(output)

    async def _collect_sse_response(self, resp: httpx.Response, req_id: int) -> dict:
        """在 SSE 流里找 id 匹配、带 result/error 的那条 JSON-RPC 响应。"""
        async for raw in resp.aiter_bytes():
            for line in raw.decode(errors="ignore").splitlines():
                if not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if obj.get("id") == req_id and ("result" in obj or "error" in obj):
                    return obj
        raise McpTimeout(f"sse:{req_id}", 0.0)

    async def notify(self, method: str, params: dict | None = None) -> None:
        if self._client is None:
            raise RuntimeError("http transport 未 connect")
        request_msg = build_notification(method, params)
        await self._client.post(self._url, json=request_msg, headers=self._headers())

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self._session_id = None