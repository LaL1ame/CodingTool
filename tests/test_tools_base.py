"""测试 Tool ABC + validate_path。"""

import pytest
from pathlib import Path

from codepilot.tools.base import Tool, validate_path


class _MinimalTool(Tool):
    @property
    def name(self): return "minimal"
    @property
    def description(self): return "desc"
    @property
    def parameters(self): return {"type": "object", "properties": {}}

    async def execute(self, **kwargs):
        from codepilot.protocols import ToolResult
        return ToolResult(call_id="", name=self.name, output="done")


class _IncompleteTool(Tool):
    @property
    def name(self): return "incomplete"


def test_minimal_tool_instantiation():
    t = _MinimalTool()
    assert t.name == "minimal"
    assert t.parameters == {"type": "object", "properties": {}}


def test_tool_abc_enforces_interface():
    with pytest.raises(TypeError):
        _IncompleteTool()


def test_validate_path_within_workspace(workspace):
    result = validate_path("readme.txt", workspace)
    assert result == (workspace / "readme.txt").resolve()


def test_validate_path_nested(workspace):
    (workspace / "sub").mkdir(exist_ok=True)
    result = validate_path("sub/nested.py", workspace)
    assert result == (workspace / "sub" / "nested.py").resolve()


def test_validate_path_traversal_rejected(workspace):
    with pytest.raises(ValueError, match="超出工作目录"):
        validate_path("../etc/passwd", workspace)


def test_validate_path_double_traversal(workspace):
    with pytest.raises(ValueError, match="超出工作目录"):
        validate_path("../../Windows/System32/config", workspace)


def test_validate_path_absolute_rejected(workspace):
    with pytest.raises(ValueError, match="超出工作目录"):
        validate_path("/etc/passwd", workspace)


def test_validate_path_dot(workspace):
    result = validate_path(".", workspace)
    assert result == workspace.resolve()
