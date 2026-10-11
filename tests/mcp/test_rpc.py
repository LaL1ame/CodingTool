"""rpc.py(JSON-RPC 2.0 信封)单元测试。"""

import pytest

from codepilot.mcp import rpc


def test_build_request_has_full_envelope():
    m = rpc.build_request(7, "initialize", {"protocolVersion": "1"})
    assert m["jsonrpc"] == "2.0"
    assert m["id"] == 7
    assert m["method"] == "initialize"
    assert m["params"] == {"protocolVersion": "1"}


def test_build_request_omits_empty_params():
    m = rpc.build_request(1, "tools/list", None)
    assert "params" not in m  # 无 params 时不应带空参键


def test_build_notification_has_no_id():
    m = rpc.build_notification("notifications/initialized")
    assert "id" not in m
    assert m["method"] == "notifications/initialized"


def test_parse_response_returns_result_when_no_error():
    msg = rpc.build_response(3, {"ok": True})
    assert rpc.parse_response(msg) == {"ok": True}


def test_parse_response_raises_on_error_envelope():
    msg = rpc.build_error(3, -32601, "method not found")
    with pytest.raises(rpc.RpcError) as exc:
        rpc.parse_response(msg)
    assert exc.value.code == -32601


def test_is_error_detects_error_vs_result():
    assert rpc.is_error(rpc.build_error(1, -1, "boom"))
    assert not rpc.is_error(rpc.build_response(1, "fine"))