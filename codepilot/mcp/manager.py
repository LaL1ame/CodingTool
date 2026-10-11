"""编排层:McpManager —— 多个 server 的连接、隔离、缓存与工具注册。

职责边界:只管"哪几个 server 活着、谁注册了哪些工具";单个 server 的
连通性由各自 try/except 隔离(N1),失败仅记日志,不外溢拖垮整套启动。
"""
from __future__ import annotations

import asyncio
import logging

from codepilot.config import McpServerSpec
from codepilot.mcp.adapter import McpToolAdapter
from codepilot.mcp.client import McpClient
from codepilot.mcp.transport.base import Transport
from codepilot.mcp.transport.http import HttpTransport
from codepilot.mcp.transport.stdio import StdioTransport
from codepilot.tools.registry import ToolRegistry

logger = logging.getLogger("codepilot.mcp")


class McpManager:
    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry
        self._clients: list[McpClient] = []

    @staticmethod
    def _build_transport(spec: McpServerSpec) -> Transport:
        if spec.type == "stdio":
            return StdioTransport(spec.command or "", spec.args, spec.env)
        return HttpTransport(spec.url or "", spec.headers)

    async def _connect_one(self, spec: McpServerSpec) -> int:
        """连接单个 server + 发现 + 注册其工具。失败返回 0 并记日志(N1)。

        关键:client 只有成功时才交给 close_all(进 _clients);失败/被取消时
        必须在 finally 就地关掉已拉起的子进程/传输,否则泄漏,Windows 退出时
        会刷一堆 asyncio.__del__ 告警。
        """
        client: McpClient | None = None
        appended = False
        try:
            transport = self._build_transport(spec)
            client = McpClient(spec.name, transport)
            await client.start()
            tools = await client.list_tools()
            self._clients.append(client)
            appended = True
        except Exception as e:
            logger.warning("MCP server %r 连接/发现失败,跳过: %s", spec.name, e)
            return 0
        finally:
            if client is not None and not appended:
                try:
                    await client.close()
                except Exception:
                    pass

        registered = 0
        for tool_name, desc, schema in tools:
            try:
                adapter = McpToolAdapter(
                    spec.name, tool_name, desc, schema, client, spec.read_only)
                self._registry.register(adapter)
                registered += 1
            except Exception as e:
                logger.warning("server %r 工具 %r 注册失败: %s", spec.name, tool_name, e)
        logger.info("MCP server %r 注册了 %d/%d 个工具",
                    spec.name, registered, len(tools))
        return registered

    async def connect_all(self, specs: list[McpServerSpec]) -> int:
        """并发连接各 server(各自隔离),返回成功注册的工具总数。"""
        results = await asyncio.gather(*(self._connect_one(s) for s in specs))
        return sum(results)

    async def close_all(self) -> None:
        """关闭所有存活连接,个别失败吞掉。幂等。"""
        for client in self._clients:
            try:
                await client.close()
            except Exception:
                pass
        self._clients.clear()