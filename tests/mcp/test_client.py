"""McpClient(会话层)单元测试:三步握手 + list/call 映射(AC4)。"""

from tests.mcp.fake_transport import FakeMcpTransport

from codepilot.mcp.client import McpClient


async def test_start_performs_handshake_and_notification():
    ft = FakeMcpTransport()
    c = McpClient("srv", ft)
    info = await c.start()
    assert info["serverInfo"]["name"] == "fake"
    methods = [m for m, _ in ft.calls]
    assert "initialize" in methods
    assert any(m.startswith("notify:") for m in methods)  # initialized 通知


async def test_list_tools_returns_tuples():
    ft = FakeMcpTransport()
    c = McpClient("srv", ft)
    await c.start()
    tools = await c.list_tools()
    assert tools[0][0] == "sum"
    assert tools[0][1] == "add"


async def test_call_tool_returns_flattened_text():
    ft = FakeMcpTransport()
    c = McpClient("srv", ft)
    res = await c.call_tool("sum", {"a": 1, "b": 2})
    assert res.output == "ok"
    assert res.error is None


async def test_call_tool_error_sets_error_field():
    ft = FakeMcpTransport(
        call_result={"content": [{"type": "text", "text": "denied"}], "isError": True})
    c = McpClient("srv", ft)
    res = await c.call_tool("rm", {})
    assert res.error == "denied"


async def test_close_forwards_to_transport():
    ft = FakeMcpTransport()
    c = McpClient("srv", ft)
    await c.close()
    assert ft.close_called is True