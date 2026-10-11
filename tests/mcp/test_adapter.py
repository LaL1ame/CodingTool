"""McpToolAdapter(适配层)单元测试:命名/副作用/execute→ToolResult(AC5,决策3/4)。"""

from tests.mcp.fake_transport import FakeMcpTransport

from codepilot.mcp.adapter import McpToolAdapter
from codepilot.mcp.client import McpClient
from codepilot.protocols import ToolResult


def _make(read_only=False):
    client = McpClient("github", FakeMcpTransport())
    return McpToolAdapter("github", "create_issue", "create an issue",
                          {"type": "object"}, client, read_only=read_only)


def test_name_is_namespaced_by_server():
    a = _make()
    assert a.name == "github.create_issue"      # AC5 / 决策3


def test_side_effect_default_true_readonly_false():
    assert _make().side_effect is True            # 决策4:默认有副作用
    assert _make(read_only=True).side_effect is False  # read_only → 只读


async def test_execute_returns_tool_result_on_success():
    a = _make()
    res = await a.execute(title="typo")
    assert isinstance(res, ToolResult)
    assert res.output == "ok"
    assert res.error is None


async def test_execute_maps_server_error_to_error():
    client = McpClient("srv", FakeMcpTransport(
        call_result={"content": [{"type": "text", "text": "permission denied"}],
                     "isError": True}))
    a = McpToolAdapter("srv", "rm", "x", {"type": "object"}, client)
    res = await a.execute()
    assert res.error is not None
    assert "permission denied" in res.error