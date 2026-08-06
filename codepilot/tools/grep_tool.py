"""搜索代码内容工具 — 正则匹配文件内容。"""

import re
from pathlib import Path

from codepilot.protocols import ToolResult
from codepilot.tools.base import Tool

GREP_LIMIT = 250


class GrepTool(Tool):
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    @property
    def name(self) -> str:
        return "grep"

    @property
    def description(self) -> str:
        return "用正则表达式搜索文件内容。返回 file_path:lineno:content 格式。默认大小写敏感，可设置 ignore_case=True。自动跳过 .git 目录。"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "正则表达式搜索模式。"},
                "ignore_case": {"type": "boolean", "default": False, "description": "设为 true 则忽略大小写。"},
                "path": {"type": "string", "description": "搜索子目录（默认 '.'）。"},
            },
            "required": ["pattern"],
        }

    async def execute(self, pattern: str, ignore_case: bool = False, path: str = ".") -> ToolResult:
        try:
            flags = re.IGNORECASE if ignore_case else 0
            regex = re.compile(pattern, flags)
        except re.error as e:
            return ToolResult(call_id="", name=self.name, error=f"无效的正则表达式: {e}")

        search_root = self.workspace / path
        if not search_root.exists():
            return ToolResult(call_id="", name=self.name, error=f"路径不存在: {path}")

        results = []
        try:
            for p in search_root.rglob("*"):
                if ".git" in p.parts:
                    continue
                if not p.is_file():
                    continue
                try:
                    text = p.read_text(encoding="utf-8")
                except (UnicodeDecodeError, PermissionError):
                    continue
                for lineno, line in enumerate(text.split("\n"), start=1):
                    if regex.search(line):
                        rel = str(p.relative_to(self.workspace))
                        results.append(f"{rel}:{lineno}:{line}")
                        if len(results) >= GREP_LIMIT + 1:
                            break
                if len(results) >= GREP_LIMIT + 1:
                    break
        except Exception as e:
            return ToolResult(call_id="", name=self.name, error=f"搜索过程出错: {e}")

        total = len(results)
        truncated = total > GREP_LIMIT
        if truncated:
            results = results[:GREP_LIMIT]
        output = "\n".join(results) if results else "(no matches)"
        if truncated:
            output += f"\n\n[truncated: {total} matches total, showing first {GREP_LIMIT}]"
        return ToolResult(call_id="", name=self.name, output=output, truncated=truncated, total_items=total if truncated else None)
