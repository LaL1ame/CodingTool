"""适配层:把发现的一个远端 MCP 工具包装成 codepilot 的 Tool 接口。

职责边界:只做"单工具"的接口对齐;控制权(尤其权限门控)由 Agent 在
_execute_one 里对**所有**工具统一执行,本层不重复实现权限。
"""
from __future__ import annotations

from codepilot.mcp.client import McpClient
from codepilot.protocols import ToolResult
from codepilot.tools.base import Tool


class McpToolAdapter(Tool):
    def __init__(
        self,
        server: str,
        tool_name: str,
        description: str,
        input_schema: dict,
        client: McpClient,
        read_only: bool = False,
    ) -> None:
        self._server = server
        self._tool_name = tool_name
        self._description = description
        self._input_schema = input_schema
        self._client = client
        self._read_only = read_only

    @property
    def name(self) -> str:
        return f"{self._server}.{self._tool_name}"  # 决策3:命名空间防撞

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict:
        return self._input_schema  # MCP inputSchema 即 JSON Schema

    @property
    def side_effect(self) -> bool:
        return not self._read_only  # 决策4:默认有副作用,read_only 才只读

    async def execute(self, **kwargs) -> ToolResult:
        try:
            result = await self._client.call_tool(self._tool_name, kwargs)
            if result.error:
                return ToolResult(call_id="", name=self.name,
                                  error=f"{self.name}: {result.error}")
            return ToolResult(call_id="", name=self.name, output=result.output)
        except Exception as e:
            return ToolResult(call_id="", name=self.name,
                              error=f"{self.name}: {e}")