"""Tab 补全候选弹菜单(state14, F6)。

多命中时用 `ModalScreen` + 原生 `ListView`(一等公民控件)列出候选:
↑↓ 移动(原生)、Enter 选中、Esc 取消。选中后 dismiss(所选命令名);取消 dismiss(None)。
"""
from __future__ import annotations

from collections.abc import Callable

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, ListItem, ListView, Static


class CompletionScreen(ModalScreen[str | None]):
    """命令补全菜单。返回:选中命令名,或 None 表示取消。"""

    BINDINGS = [("escape", "cancel", "取消")]

    def __init__(self, candidates: list[str],
                 on_pick: Callable[[str], None] | None = None) -> None:
        super().__init__()
        self._candidates = list(candidates)
        self._on_pick = on_pick

    def compose(self) -> ComposeResult:
        with Vertical(id="completion-box"):
            yield Static("命令候选(↑↓ 选择 · Enter 确定 · Esc 取消)", id="completion-title")
            yield ListView(
                *[ListItem(Label(f"/{c}")) for c in self._candidates],
                id="completion-list",
            )

    def on_mount(self) -> None:
        self.query_one(ListView).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        idx = self.query_one(ListView).index
        if idx is None or not (0 <= idx < len(self._candidates)):
            return
        name = self._candidates[idx]
        if self._on_pick is not None:
            self._on_pick(name)
        self.dismiss(name)