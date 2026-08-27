"""工具注册中心 — 集中登记工具 + 协议格式转换。"""

from codepilot.tools.base import Tool


class ToolRegistry:
    """负责集中登记工具、按名查找、转换为 Anthropic/OpenAI 原生格式。"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list_all(self) -> list[Tool]:
        return list(self._tools.values())

    def list_read_only(self) -> list[Tool]:
        """返回无副作用（只读）的工具子集，供计划模式过滤。"""
        return [t for t in self._tools.values() if not t.side_effect]

    def to_anthropic_tools(self) -> list[dict]:
        return [
            {"name": t.name, "description": t.description, "input_schema": t.parameters}
            for t in self._tools.values()
        ]

    def to_openai_tools(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in self._tools.values()
        ]
