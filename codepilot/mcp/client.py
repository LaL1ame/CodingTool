"""会话层:McpClient —— 一个 server 的三步握手与工具调用。

职责边界:只懂 MCP 协议次序(initialize → notifications/initialized →
tools/list → tools/call),不懂 framing/HTTP;transport 由外层注入。
"""
from __future__ import annotations

from dataclasses import dataclass

from codepilot.mcp.transport.base import Transport


@dataclass
class MCPCallResult:
    """一次 tools/call 的结果(展平后的纯文本)。"""
    output: str
    error: str | None = None


_PROTOCOL_VERSION = "2024-11-05"
_CLIENT_INFO = {"name": "codepilot", "version": "0.2.0"}


class McpClient:
    def __init__(self, name: str, transport: Transport) -> None:
        self._name = name
        self._transport = transport
        self._info: dict | None = None

    @property
    def server_name(self) -> str:
        return self._name

    async def start(self) -> dict:
        """连接并完成 initialize 握手 + initialized 通知。返回 server 能力/信息。"""
        await self._transport.connect()
        self._info = await self._transport.request("initialize", {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": _CLIENT_INFO,
        })
        await self._transport.notify("notifications/initialized")
        return self._info

    async def list_tools(self) -> list[tuple[str, str, dict]]:
        """tools/list → [(name, description, inputSchema)]。"""
        result = await self._transport.request("tools/list")
        return [
            (t.get("name", ""), t.get("description", ""), t.get("inputSchema", {}))
            for t in result.get("tools", [])
        ]

    async def call_tool(self, name: str, arguments: dict) -> MCPCallResult:
        """tools/call → MCPCallResult(isError 时填 error)。"""
        raw = await self._transport.request(
            "tools/call", {"name": name, "arguments": arguments})
        parts = []
        for block in raw.get("content", []):
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
            else:
                parts.append(str(block))
        text = "\n".join(p for p in parts if p)
        is_error = bool(raw.get("isError", False))
        return MCPCallResult(output=text, error=text if is_error else None)

    async def close(self) -> None:
        await self._transport.close()
        self._info = None