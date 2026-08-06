"""编辑文件工具 — 原文精确匹配替换。"""

from pathlib import Path

from codepilot.protocols import ToolResult
from codepilot.tools.base import Tool, validate_path


class EditTool(Tool):
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    @property
    def name(self) -> str:
        return "edit"

    @property
    def description(self) -> str:
        return "在文件中做精确匹配替换。找到 old_string 并替换为 new_string。old_string 必须在文件中有唯一匹配。建议先用 read 确认文件现状。"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "要编辑的文件相对路径。"},
                "old_string": {"type": "string", "description": "要被替换的原文，必须在文件中唯一匹配。"},
                "new_string": {"type": "string", "description": "替换后的新文本。"},
            },
            "required": ["file_path", "old_string", "new_string"],
        }

    async def execute(self, file_path: str, old_string: str, new_string: str) -> ToolResult:
        try:
            resolved = validate_path(file_path, self.workspace)
        except ValueError as e:
            return ToolResult(call_id="", name=self.name, error=str(e))
        try:
            content = resolved.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ToolResult(call_id="", name=self.name, error=f"文件不存在: {file_path}")
        except Exception as e:
            return ToolResult(call_id="", name=self.name, error=f"读取文件失败: {e}")

        count = content.count(old_string)
        if count == 0:
            return ToolResult(call_id="", name=self.name, error="未找到匹配文本。请用 read 确认文件当前内容，确保 old_string 与文件中原文完全一致（含缩进和空白字符）。")
        if count >= 2:
            lines = content.split("\n")
            details = []
            for lineno, line in enumerate(lines, start=1):
                if old_string in line:
                    start = max(0, lineno - 3)
                    end = min(len(lines), lineno + 2)
                    ctx = "\n".join(f"  {i:>6}\t{l}" for i, l in enumerate(lines[start:end], start=start + 1))
                    details.append(f"  第 {lineno} 行:\n{ctx}")
            return ToolResult(call_id="", name=self.name, error=f"匹配到 {count} 处。请缩小范围使匹配唯一。\n" + "\n".join(details))

        new_content = content.replace(old_string, new_string, 1)
        idx = content.index(old_string)
        lineno = content[:idx].count("\n") + 1
        try:
            resolved.write_text(new_content, encoding="utf-8")
            return ToolResult(call_id="", name=self.name, output=f"Replaced 1 occurrence in {file_path} at line {lineno}")
        except Exception as e:
            return ToolResult(call_id="", name=self.name, error=f"写入文件失败: {e}")
