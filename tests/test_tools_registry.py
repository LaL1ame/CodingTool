"""测试 ToolRegistry。"""

import pytest

from codepilot.tools.base import Tool
from codepilot.tools.registry import ToolRegistry
from codepilot.protocols import ToolResult


class _FakeTool(Tool):
    def __init__(self, name: str, side_effect: bool = False):
        self._name = name
        self._side_effect = side_effect

    @property
    def name(self): return self._name
    @property
    def description(self): return f"Fake: {self._name}"
    @property
    def parameters(self): return {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}
    @property
    def side_effect(self): return self._side_effect

    async def execute(self, **kwargs):
        return ToolResult(call_id="", name=self._name, output="ok")


@pytest.fixture
def registry():
    r = ToolRegistry()
    r.register(_FakeTool("read"))
    r.register(_FakeTool("write"))
    return r


class TestRegistryBasics:
    def test_register_and_get(self, registry):
        assert registry.get("read").name == "read"

    def test_get_nonexistent(self, registry):
        assert registry.get("nonexistent") is None

    def test_list_all(self, registry):
        assert len(registry.list_all()) == 2

    def test_duplicate_overwrites(self, registry):
        registry.register(_FakeTool("read"))
        assert len(registry.list_all()) == 2

    def test_list_read_only(self):
        r = ToolRegistry()
        r.register(_FakeTool("read"))
        r.register(_FakeTool("write", side_effect=True))
        assert [t.name for t in r.list_read_only()] == ["read"]


class TestAnthropicFormat:
    def test_format_structure(self, registry):
        tools = registry.to_anthropic_tools()
        assert len(tools) == 2
        for t in tools:
            assert "name" in t
            assert "description" in t
            assert "input_schema" in t

    def test_empty_registry(self):
        assert ToolRegistry().to_anthropic_tools() == []


class TestOpenAIFormat:
    def test_format_structure(self, registry):
        tools = registry.to_openai_tools()
        assert len(tools) == 2
        for t in tools:
            assert t["type"] == "function"
            assert "function" in t

    def test_empty_registry(self):
        assert ToolRegistry().to_openai_tools() == []
