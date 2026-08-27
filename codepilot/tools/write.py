"""写文件工具 — 创建或覆盖文件。"""

from pathlib import Path

from codepilot.protocols import ToolResult
from codepilot.tools.base import Tool, validate_path


class WriteTool(Tool):
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    @property
    def name(self) -> str:
        return "write"

    @property
    def description(self) -> str:
        return "创建或覆盖文件。如果文件已存在则覆盖其内容。会自动创建不存在的父目录。"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "要写入的文件相对路径。"},
                "content": {"type": "string", "description": "要写入的文件完整内容。"},
            },
            "required": ["file_path", "content"],
        }

    @property
    def side_effect(self) -> bool:
        return True

    async def execute(self, file_path: str, content: str) -> ToolResult:
        try:
            resolved = validate_path(file_path, self.workspace)
        except ValueError as e:
            return ToolResult(call_id="", name=self.name, error=str(e))
        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            existed = resolved.exists()
            resolved.write_text(content, encoding="utf-8")
            size = len(content.encode("utf-8"))
            verb = "Overwrote" if existed else "Wrote"
            return ToolResult(call_id="", name=self.name, output=f"{verb} {size} bytes to {file_path}")
        except PermissionError:
            return ToolResult(call_id="", name=self.name, error=f"无权限写入: {file_path}")
        except Exception as e:
            return ToolResult(call_id="", name=self.name, error=f"写入文件失败: {e}")
