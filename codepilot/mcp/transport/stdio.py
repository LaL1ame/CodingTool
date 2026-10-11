"""stdio 传输:以子进程拉起本地 MCP server,经 stdin/stdout 管道收发。

采用 MCP/LSP 标准的 `Content-Length` 定长帧(非换行 JSON)防粘连:
发出去的是 `Content-Length: {n}\\r\\n\\r\\n{json}`;读侧按头解析长度、精确读够 n 字节。

id 配对:一个后台读者任务解析入炉帧,按 `id` 路由给对应 pending future;
`request` 注册 future 等结果(带超时),响应是错误信封 → parse_response 抛 RpcError。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from typing import Any

from codepilot.mcp.rpc import build_notification, build_request, parse_response
from codepilot.mcp.transport.base import McpTimeout, Transport

logger = logging.getLogger("codepilot.mcp.stdio")

_ON_WINDOWS = os.name == "nt"


class StdioTransport(Transport):
    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._command = command
        self._args = args or []
        self._env = env
        self._timeout = timeout
        self._proc: asyncio.subprocess.Process | None = None
        self._read_task: asyncio.Task | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 1

    # —— 编码/解码(定长帧) ——

    @staticmethod
    def _encode(msg: dict) -> bytes:
        body = json.dumps(msg, ensure_ascii=False).encode("utf-8")
        head = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        return head + body

    async def _read_frame(self) -> dict:
        """读一帧并反序列化为 dict。stream 关闭→抛 EOFError。"""
        assert self._proc is not None and self._proc.stdout is not None
        content_length: int | None = None
        while True:
            line = await self._proc.stdout.readline()
            if not line:
                raise EOFError("子进程 stdout 已关闭")
            text = line.decode(errors="ignore").rstrip("\r\n")
            if text == "":
                break
            if text.lower().startswith("content-length:"):
                content_length = int(text.split(":", 1)[1].strip())
        if content_length is None:
            raise EOFError(f"帧缺少 Content-Length:\n{text!r}")
        body = await self._proc.stdout.readexactly(content_length)
        return json.loads(body)

    async def _write(self, payload: bytes) -> None:
        assert self._proc is not None and self._proc.stdin is not None
        self._proc.stdin.write(payload)
        await self._proc.stdin.drain()

    # —— 后台读者:解析帧并按 id 路由 ——

    async def _read_loop(self) -> None:
        try:
            while True:
                msg = await self._read_frame()
                req_id = msg.get("id")
                if req_id is not None and req_id in self._pending:
                    fut = self._pending.pop(req_id)
                    if not fut.done():
                        fut.set_result(msg)
        except (EOFError, asyncio.CancelledError):
            pass
        except Exception as e:  # 子进程异常终止等情况
            logger.warning("stdio 读者退出: %s", e)
        finally:
            # 连接断了:让所有仍挂起的请求醒过来,别无限等
            for fut in list(self._pending.values()):
                if not fut.done():
                    fut.set_exception(McpTimeout("__closed__", self._timeout))
            self._pending.clear()

    # —— Transport 接口 ——

    def _resolve_argv(self) -> list[str]:
        """解析启动命令。Windows 下 npx/npm 等是 .cmd/.bat 批处理,
        create_subprocess_exec 只能直接启 .exe,故批处理须经 cmd.exe /c 包一层。"""
        if _ON_WINDOWS:
            resolved = shutil.which(self._command)
            if resolved and resolved.lower().endswith((".cmd", ".bat")):
                comspec = os.environ.get("COMSPEC") or "cmd.exe"
                return [comspec, "/c", self._command, *self._args]
        return [self._command, *self._args]

    async def connect(self) -> None:
        env = dict(os.environ)
        if self._env:
            env.update(self._env)
        argv = self._resolve_argv()
        self._proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        self._read_task = asyncio.create_task(self._read_loop())

    def _next_request_id(self) -> int:
        req_id = self._next_id
        self._next_id += 1
        return req_id

    async def request(self, method: str, params: dict | None = None) -> Any:
        if self._proc is None:
            raise RuntimeError("stdlib transport 未 connect")
        req_id = self._next_request_id()
        message = build_request(req_id, method, params)
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = future
        await self._write(self._encode(message))
        try:
            raw = await asyncio.wait_for(future, timeout=self._timeout)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise McpTimeout(method, self._timeout) from None
        return parse_response(raw)

    async def notify(self, method: str, params: dict | None = None) -> None:
        if self._proc is None:
            raise RuntimeError("stdlib transport 未 connect")
        await self._write(self._encode(build_notification(method, params)))

    async def close(self) -> None:
        if self._read_task is not None:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            self._read_task = None

        proc, self._proc = self._proc, None
        if proc is None:
            return
        # 先关管道,避免 Windows Proactor 在退出时报告 unclosed transport
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except (BrokenPipeError, OSError, ValueError):
                    pass
        try:
            proc.terminate()
            await proc.wait()
        except (ProcessLookupError, OSError, ValueError):
            pass
        # 回收底层子进程 transport(Proactor 需显式 close 才不泄漏)
        proc_transport = getattr(proc, "_transport", None)
        if proc_transport is not None:
            try:
                proc_transport.close()
            except Exception:
                pass