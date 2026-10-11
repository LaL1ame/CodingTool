"""MCP 客户端子包。

按 spec.md(state8)实现,分层:transport(传输)→ client(会话)→
adapter(工具适配)→ manager(编排)。依赖关系单向无环:
config → transport → client → adapter → manager。
"""
from codepilot.mcp.adapter import McpToolAdapter
from codepilot.mcp.manager import McpManager

__all__ = ["McpManager", "McpToolAdapter"]