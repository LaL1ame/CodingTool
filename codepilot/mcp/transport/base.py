"""Transport 抽象基类 —— 按 JSON-RPC 消息粒度收发。

具体传输(stdio/http)实现 connect/request/notify/close:
- request:组装带自增 id 的请求,发送,按 id 等到对应响应,返回 result;
          响应是错误信封 → 抛 RpcError;超时 → 抛 McpTimeout。
- notify :fire-and-forget 通知,发送即返回,不等响应。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from codepilot.mcp.rpc import RpcError


class McpTimeout(Exception):
    """MCP 请求在超时时间内未收到配对响应。"""

    def __init__(self, method: str, timeout: float) -> None:
        super().__init__(f"MCP 请求超时({timeout:.0f}s): {method}")
        self.method = method
        self.timeout = timeout


class Transport(ABC):
    """消息粒度的双向通道。一个实例对应一条到某个 server 的活连接。"""

    @abstractmethod
    async def connect(self) -> None:
        """建立底层连接(拉起子进程 / 初始化 httpx client)。"""
        ...

    @abstractmethod
    async def request(self, method: str, params: dict | None = None) -> Any:
        """发请求并等待按 id 配对的 result;错误 → RpcError;超时 → McpTimeout。"""
        ...

    @abstractmethod
    async def notify(self, method: str, params: dict | None = None) -> None:
        """发送通知,不等待响应。"""
        ...

    @abstractmethod
    async def close(self) -> None:
        """关闭连接并释放资源。幂等。"""
        ...