"""JSON-RPC 2.0 信封的构造与解析辅助(MCP 复用)。

纯函数、无 IO,便于单测。传输层(stdio/http)用它组装/拆解消息。
本模块不做网络;只负责把一个字典包装成合法 JSON-RPC 2.0 请求/响应,
以及从响应里安全取出 result 或抛 RpcError。
"""
from __future__ import annotations

from typing import Any


class RpcError(Exception):
    """JSON-RPC 错误响应。code 采用规范保留区间 -32768..-32600。"""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


def build_request(req_id: int, method: str, params: dict | None = None) -> dict:
    """构造一个 JSON-RPC 2.0 请求信封(带 id)。"""
    msg: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params:
        msg["params"] = params
    return msg


def build_notification(method: str, params: dict | None = None) -> dict:
    """构造通知(无 id,服务端不返回响应)。"""
    msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params:
        msg["params"] = params
    return msg


def build_response(req_id: int, result: Any) -> dict:
    """构造成功响应信封(供测试用的假 server 端)。"""
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def build_error(req_id: int, code: int, message: str, data: Any = None) -> dict:
    """构造错误响应信封(供测试用的假 server 端)。"""
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


def is_error(msg: dict) -> bool:
    """判断一个信封是否为错误响应。"""
    return "error" in msg


def parse_response(msg: dict) -> Any:
    """从响应信封取 result;若是错误响应则抛 RpcError。消息必须含 id 匹配。

    供传输层在用 id 配到对应响应后调用。
    """
    if "error" in msg:
        e = msg["error"]
        raise RpcError(
            e.get("code", 0),
            e.get("message", "unknown error"),
            e.get("data"),
        )
    return msg.get("result")