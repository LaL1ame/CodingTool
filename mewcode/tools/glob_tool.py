"""搜索文件工具 — glob 模式匹配。"""

from pathlib import Path

from mewcode.protocols import ToolResult
from mewcode.tools.base import Tool

GLOB_LIMIT = 500


class GlobTool(Tool):
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    @property
    def name(self) -> str:
        return "glob"

    @property
    def description(self) -> str:
        return "按 glob 模式搜索文件。支持 **/*.py、src/**/*.ts 等标准 glob 语法。返回按修改时间排序的相对路径列表。"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob 模式，如 '**/*.py'。使用 ** 递归匹配子目录。"},
            },
            "required": ["pattern"],
        }

    async def execute(self, pattern: str) -> ToolResult:
        try:
            matches = []
            for p in self.workspace.rglob(pattern):
                if ".git" in p.parts:
                    continue
                if p.is_file():
                    matches.append(p)
            matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            total = len(matches)
            truncated = total > GLOB_LIMIT
            if truncated:
                matches = matches[:GLOB_LIMIT]
            lines = [str(m.relative_to(self.workspace)) for m in matches]
            output = "\n".join(lines) if lines else "(no matches)"
            if truncated:
                output += f"\n\n[truncated: {total} files total, showing first {GLOB_LIMIT}]"
            return ToolResult(call_id="", name=self.name, output=output, truncated=truncated, total_items=total if truncated else None)
        except Exception as e:
            return ToolResult(call_id="", name=self.name, error=f"Glob 搜索失败: {e}")
