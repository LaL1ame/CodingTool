"""StdioTransport 单元测试:拉起假 server,验证握手/调用/并发配对/超时。"""

import asyncio
import sys
from pathlib import Path

import pytest

from codepilot.mcp.rpc import RpcError
from codepilot.mcp.transport import stdio as stdio_mod
from codepilot.mcp.transport.base import McpTimeout
from codepilot.mcp.transport.stdio import StdioTransport

FAKE = str(Path(__file__).resolve().parent / "fake_mcp_server.py")


async def test_connect_initialize_and_call():
    t = StdioTransport(sys.executable, [FAKE])
    await t.connect()
    try:
        info = await t.request("initialize", {"protocolVersion": "2024-11-05"})
        assert info["serverInfo"]["name"] == "fake"
        tools = await t.request("tools/list")
        assert tools["tools"][0]["name"] == "sum"
        res = await t.request("tools/call", {"name": "sum", "arguments": {"a": 2, "b": 3}})
        assert res["content"][0]["text"] == "5"
    finally:
        await t.close()


async def test_concurrent_calls_paired_by_id():
    """AC3:并发多个调用,按 id 正确回对,不串答。"""
    t = StdioTransport(sys.executable, [FAKE])
    await t.connect()
    try:
        async def one(i):
            r = await t.request(
                "tools/call", {"name": "sum", "arguments": {"a": i, "b": 1}})
            return int(r["content"][0]["text"])
        results = await asyncio.gather(*(one(i) for i in range(5)))
        assert results == [i + 1 for i in range(5)]
    finally:
        await t.close()


async def test_error_envelope_raises_rpc_error():
    t = StdioTransport(sys.executable, [FAKE])
    await t.connect()
    try:
        with pytest.raises(RpcError):
            await t.request("no/such/method")
    finally:
        await t.close()


def test_windows_batch_resolution_wraps_in_cmd(monkeypatch):
    """Windows 下 npx 是 .cmd,应经 cmd.exe /c 执行(create_subprocess_exec 不能直接跑批处理)。"""
    t = StdioTransport("npx", ["-y", "@mcp/foo"])
    monkeypatch.setattr(stdio_mod, "_ON_WINDOWS", True)
    monkeypatch.setattr(stdio_mod.shutil, "which", lambda c: "C:\\x\\npx.cmd")
    argv = t._resolve_argv()
    assert "/c" in argv
    assert argv[-3:] == ["npx", "-y", "@mcp/foo"]


def test_non_batch_returns_as_is(monkeypatch):
    t = StdioTransport("python", ["-V"])
    monkeypatch.setattr(stdio_mod, "_ON_WINDOWS", True)
    monkeypatch.setattr(stdio_mod.shutil, "which", lambda c: "C:\\x\\python.exe")
    assert t._resolve_argv() == ["python", "-V"]


async def test_timeout_raises_mcp_timeout():
    # 一个只会睡觉、不回帧的子进程 → 请求超时
    t = StdioTransport(sys.executable, ["-c", "import time; time.sleep(30)"], timeout=0.3)
    await t.connect()
    try:
        with pytest.raises(McpTimeout):
            await t.request("anything")
    finally:
        await t.close()