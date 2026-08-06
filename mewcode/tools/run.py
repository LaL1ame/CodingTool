"""执行命令工具 — 带用户确认的 Shell 命令执行。"""

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

from mewcode.protocols import ToolResult
from mewcode.tools.base import Tool

OUTPUT_LIMIT = 8000


class RunTool(Tool):
    def __init__(
        self,
        workspace: Path,
        timeout: float = 120.0,
        confirm_callback: Callable[[str], Awaitable[bool]] | None = None,
    ) -> None:
        self.workspace = workspace
        self.timeout = timeout
        self.confirm_callback = confirm_callback

    @property
    def name(self) -> str:
        return "run"

    @property
    def description(self) -> str:
        return "在 Shell 中执行命令。返回 stdout、stderr 和退出码。执行前会请求用户确认。超时 120 秒后自动终止。"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的 Shell 命令。"},
            },
            "required": ["command"],
        }

    async def execute(self, command: str, confirm_callback=None) -> ToolResult:
        cb = confirm_callback or self.confirm_callback
        if cb is not None:
            try:
                result = cb(command)
                if hasattr(result, "__await__"):
                    approved = await result
                else:
                    approved = result
            except Exception as e:
                return ToolResult(call_id="", name=self.name, error=f"确认回调异常: {e}")
            if not approved:
                return ToolResult(call_id="", name=self.name, error="用户拒绝执行")

        try:
            process = await asyncio.create_subprocess_shell(
                command, cwd=str(self.workspace),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self.timeout)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                return ToolResult(call_id="", name=self.name, error=f"命令超时 ({self.timeout:.0f}s): {command[:100]}")

            out = stdout.decode("utf-8", errors="replace")
            err = stderr.decode("utf-8", errors="replace")
            exit_code = process.returncode or 0

            parts = [f"Exit code: {exit_code}"]
            if out:
                parts.append(f"STDOUT:\n{out}")
            if err:
                parts.append(f"STDERR:\n{err}")
            output = "\n".join(parts)

            truncated = len(output) > OUTPUT_LIMIT
            if truncated:
                total_len = len(output)
                output = output[:OUTPUT_LIMIT] + f"\n\n[truncated: {total_len} chars total, showing first {OUTPUT_LIMIT}]"
            return ToolResult(call_id="", name=self.name, output=output, truncated=truncated, total_items=len(output) + OUTPUT_LIMIT if truncated else None)
        except Exception as e:
            return ToolResult(call_id="", name=self.name, error=f"命令执行失败: {e}")
