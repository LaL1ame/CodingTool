"""McpManager(编排层)单元测试:注册、命名、隔离(AC1,AC5,AC7)。"""

import sys
from pathlib import Path

from codepilot.config import McpServerSpec
from codepilot.mcp.manager import McpManager
from codepilot.tools.registry import ToolRegistry

FAKE = str(Path(__file__).resolve().parent / "fake_mcp_server.py")


async def test_registers_good_server_and_isolates_bad():
    reg = ToolRegistry()
    mgr = McpManager(reg)
    specs = [
        McpServerSpec(name="good", type="stdio", command=sys.executable, args=[FAKE]),
        McpServerSpec(name="bad", type="stdio", command="definitely-not-a-cmd-xyz"),
    ]
    n = await mgr.connect_all(specs)
    assert n == 1                               # 只有 good 的工具
    assert reg.get("good.sum") is not None      # AC5:命名 server.tool
    assert reg.get("bad.sum") is None
    await mgr.close_all()


async def test_unreachable_http_server_does_not_raise():
    reg = ToolRegistry()
    mgr = McpManager(reg)
    # AC7:连不上的 server 只记日志,不崩启动、不留残留
    await mgr.connect_all([McpServerSpec(name="x", type="http",
                                         url="http://127.0.0.1:9")])
    assert len(reg.list_all()) == 0
    await mgr.close_all()


async def test_multiple_good_servers_all_register():
    reg = ToolRegistry()
    mgr = McpManager(reg)
    specs = [
        McpServerSpec(name="a", type="stdio", command=sys.executable, args=[FAKE]),
        McpServerSpec(name="b", type="stdio", command=sys.executable, args=[FAKE]),
    ]
    assert await mgr.connect_all(specs) == 2    # 两个 server 各 1 个工具
    assert reg.get("a.sum") is not None and reg.get("b.sum") is not None
    await mgr.close_all()