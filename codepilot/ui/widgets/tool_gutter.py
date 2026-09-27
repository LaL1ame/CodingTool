"""工具调用 gutter 行(Widget)。

每工具一行,按调色板着色:图标 + 名称 + 参数 + 侧栏进度态。
persistent —— 追加进滚动会话、跨轮保留(见 app)。
"""
from __future__ import annotations

from textual.widgets import Static

from codepilot.ui import palette


class ToolRow(Static):
    def __init__(self, tool_id: str, tool_name: str, params_summary: str) -> None:
        super().__init__("", id=f"tool-{tool_id}")
        self.tool_id = tool_id
        self.tool_name = tool_name
        self._markup = ""
        self.set_pending(params_summary)

    # Rich markup 前缀,主题 TEXT 由 tcss class="pill" 提供底衬
    def _base(self, body: str) -> str:
        color = palette.tool_color(self.tool_name)
        icon = palette.tool_icon(self.tool_name)
        return f"[{color}]{icon}[/{color}] [bold]{self.tool_name}[/bold] {body}"

    def _show(self, body: str) -> None:
        self._markup = self._base(body)
        self.update(self._markup)

    def set_pending(self, params_summary: str) -> None:
        self._show(f"[dim]({params_summary}) ●[/dim]")

    def set_done(self, summary: str) -> None:
        self._show(f"[green]✅ {summary}[/green]")

    def set_error(self, message: str) -> None:
        short = message[:80] + "..." if len(message) > 80 else message
        self._show(f"[bold red]✕ {short}[/bold red]")

    def line(self) -> str:
        """定型后的 markup 串,供车载入可滚动会话(RichLog)。"""
        return self._markup or self.tool_name