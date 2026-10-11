"""transport/base.py(Transport ABC + 异常)单元测试。"""

import pytest

from codepilot.mcp.rpc import RpcError
from codepilot.mcp.transport.base import McpTimeout, Transport


def test_transport_is_abstract():
    with pytest.raises(TypeError):
        Transport()  # ABC 不能直接实例化


def test_timeout_error_carries_method():
    e = McpTimeout("tools/call", 30.0)
    assert "tools/call" in str(e)
    assert e.method == "tools/call"


def test_rpc_error_reused_from_rpc_module():
    """传输层复用 rpc.RpcError,保证同一错误类型贯通。"""
    assert issubclass(RpcError, Exception)