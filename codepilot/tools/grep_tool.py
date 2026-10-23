"""搜索代码内容工具 — 正则匹配文件内容。"""

import re
from pathlib import Path

from codepilot.protocols import ToolResult
from codepilot.tools.base import Tool, is_worktree_path

GREP_LIMIT = 250


class GrepTool(Tool):
    def __init__(self, workspace: Path,
                 protected_roots: tuple[Path, ...] = ()) -> None:
        self.workspace = workspace
        self._protected_roots = tuple(Path(p) for p in protected_roots)

    def set_protected_roots(self, roots: tuple[Path, ...]) -> None:
        """注入受保护根（如团队持久化目录）；搜索根触及则拒绝（机制层强约束）。"""
        self._protected_roots = tuple(Path(p) for p in roots)

    def _guard_protected(self, search_root: Path) -> bool:
        """search_root resolve 后是否落在受保护根内（如团队存储）。"""
        if not self._protected_roots:
            return False
        try:
            resolved = search_root.resolve()
        except OSError:
            return False
        for root in self._protected_roots:
            if resolved == root or resolved.is_relative_to(root):
                return True
        return False

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
        if self._guard_protected(search_root):
            return ToolResult(
                call_id="", name=self.name,
                error=("搜索根触及受保护的团队数据目录，已拒绝："
                       f"{path}\n团队状态/消息请用 team_* 工具读取"
                       "（team_show / team_task_list / team_inbox），"
                       "禁止用 grep 直接搜团队存储文件。"))
        if not search_root.exists():
            return ToolResult(call_id="", name=self.name, error=f"路径不存在: {path}")

        results = []
        try:
            for p in search_root.rglob("*"):
                if ".git" in p.parts:
                    continue
                if is_worktree_path(p, self.workspace):
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
