"""执行命令工具 — 带用户确认的 Shell 命令执行。"""

import asyncio
import re
from pathlib import Path

from codepilot.protocols import ToolResult
from codepilot.tools.base import Tool

OUTPUT_LIMIT = 8000

# 命令 token 里的绝对路径/家目录开头的片段（如 C:\... 或 ~/.config/...）
_PATH_TOKEN_RE = re.compile(r"([A-Za-z]:[\\/]|~[/\\])")


class RunTool(Tool):
    def __init__(self, workspace: Path, timeout: float = 120.0,
                 protected_roots: tuple[Path, ...] = ()) -> None:
        self.workspace = workspace
        self.timeout = timeout
        self._protected_roots = tuple(Path(p) for p in protected_roots)

    def set_protected_roots(self, roots: tuple[Path, ...]) -> None:
        """注入受保护根（如团队持久化目录）；命令触及则拒绝（机制层强约束）。"""
        self._protected_roots = tuple(Path(p) for p in roots)

    def _guard_protected(self, command: str) -> None:
        """命令若显式触及受保护根（如团队存储）则拒绝，引导用 team_* 工具。

        命令是字符串，无法可靠解析 shell 语义；用「绝对路径/家目录开头 token」
        启发式检测，拦下 `type C:\\...\\teams\\...` 这类显式翻文件（真机暴露）。
        """
        if not self._protected_roots:
            return
        for tok in command.split():
            clean = tok.strip("\"'`")
            if not clean or not _PATH_TOKEN_RE.search(clean):
                continue  # 相对路径在 cwd 内，不可能是用户目录团队数据
            try:
                cand = Path(clean).expanduser().resolve()
            except (OSError, ValueError):
                continue
            for root in self._protected_roots:
                if cand == root or cand.is_relative_to(root):
                    raise ValueError(
                        "命令触及受保护的团队数据目录，已拒绝执行："
                        f"{clean}\n团队状态/消息请用 team_* 工具读取"
                        "（team_show / team_task_list / team_inbox），"
                        "禁止用 run 直接访问团队存储文件。")

    @property
    def name(self) -> str:
        return "run"

    @property
    def description(self) -> str:
        return "在 Shell 中执行命令。返回 stdout、stderr 和退出码。是否放行由权限系统裁决。超时 120 秒后自动终止。"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的 Shell 命令。"},
            },
            "required": ["command"],
        }

    @property
    def side_effect(self) -> bool:
        return True

    async def execute(self, command: str) -> ToolResult:
        try:
            self._guard_protected(command)
        except ValueError as e:
            return ToolResult(call_id="", name=self.name, error=str(e))
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
