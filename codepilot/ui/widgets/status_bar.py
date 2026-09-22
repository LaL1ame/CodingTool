"""底部状态栏(Widget)。

Claude Code 式底部状态线:工具轴(Plan/Build) · 放行策略(manual/auto/strict) ·
自动编辑标记([AE]) · 模型 · 累计 token · 耗时。
数据由外部注入(render_source),本身无状态,便于测试。
"""
from __future__ import annotations

from textual.widgets import Static

from codepilot.ui import palette

STATUS_BAR_ID = "status-bar"


class StatusBar(Static):
    """只读状态展示;每帧由 owner 调用 set_state(...)/update_markup 刷新。"""

    def __init__(self, text: str = "") -> None:
        super().__init__(text, id=STATUS_BAR_ID)

    @staticmethod
    def render_tokens(total: int) -> str:
        return f"↑{total}t"

    def update_state(self, *, axis: str, release: str, accept_edits: bool,
                     model: str, tokens: int, elapsed_s: float) -> None:
        """组装状态行 markup。axis/release 取 ToolAxis/ReleasePolicy.value。"""
        ac = palette.axis_color(axis)
        rc = palette.release_color(release)
        ae = palette.ACCEPT_EDITS_COLOR

        markers = [
            f"[{ac}]{axis.upper()}[/{ac}]",
            f"[{rc}]{release}[/{rc}]",
            f"[{ae}]AE[/{ae}]" if accept_edits else "AE-off",
        ]
        line = " · ".join(markers)
        line += f"  {model}  {self.render_tokens(tokens)}  ⏱{elapsed_s:.0f}s"
        self.update(line)