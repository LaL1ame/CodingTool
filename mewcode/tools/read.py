"""读文件工具 — 带行号返回文件内容。"""

from pathlib import Path

from mewcode.protocols import ToolResult
from mewcode.tools.base import Tool, validate_path

READ_LIMIT = 2000


class ReadTool(Tool):
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    @property
    def name(self) -> str:
        return "read"

    @property
    def description(self) -> str:
        return "读取文件内容。返回带行号的 cat -n 格式文本。可用 offset 和 limit 控制范围，大文件建议分次读取。"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "要读取的文件相对路径。"},
                "offset": {"type": "integer", "default": 0, "description": "起始行号（0-based）。"},
                "limit": {"type": "integer", "description": "最多读取行数。"},
            },
            "required": ["file_path"],
        }

    async def execute(self, file_path: str, offset: int = 0, limit: int | None = None) -> ToolResult:
        try:
            resolved = validate_path(file_path, self.workspace)
        except ValueError as e:
            return ToolResult(call_id="", name=self.name, error=str(e))
        try:
            content = resolved.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ToolResult(call_id="", name=self.name, error=f"文件不存在: {file_path}")
        except PermissionError:
            return ToolResult(call_id="", name=self.name, error=f"无权限读取: {file_path}")
        except Exception as e:
            return ToolResult(call_id="", name=self.name, error=f"读取文件失败: {e}")

        lines = content.split("\n")
        total_lines = len(lines)
        if limit is not None:
            lines = lines[offset:offset + limit]
        else:
            lines = lines[offset:]

        numbered = [f"{i+offset+1:>6}\t{line}" for i, line in enumerate(lines)]
        output = "\n".join(numbered)
        displayed = len(lines)

        if limit is None and total_lines > READ_LIMIT:
            output = "\n".join(output.split("\n")[:READ_LIMIT])
            output += f"\n\n[truncated: {total_lines} lines total, showing first {READ_LIMIT}]"
            return ToolResult(call_id="", name=self.name, output=output, truncated=True, total_items=total_lines)

        return ToolResult(call_id="", name=self.name, output=output)
