"""权限确认屏幕(人机回路)。

Claude Code 式排版:顶部工具路径 + 命令高亮、高亮动作区、底部快捷键 hint。
保留 4 语义选项(本次/拒绝/会话/永久)→ HumanChoice,交互语义与旧版一致。
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Static

from codepilot.permissions.policy import HumanChoice

# 工具派发也经 palTTHEME 内色;排版走 tcss id


class PermissionScreen(Screen[HumanChoice]):
    """五层防御第 5 层:规则未命中的 ask 交回用户。四选映射 HumanChoice。"""

    OPTIONS = [
        ("1", "允许本次"),
        ("2", "拒绝"),
        ("3", "本会话允许"),
        ("4", "永久允许"),
    ]
    CHOICES = [
        HumanChoice.ALLOW_THIS,
        HumanChoice.DENY,
        HumanChoice.ALLOW_SESSION,
        HumanChoice.ALLOW_FOREVER,
    ]

    def __init__(self, title: str, detail: str) -> None:
        super().__init__()
        self.title = title
        self.detail = detail
        self._cursor = 0
        self._active = False

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Static(f"⚠ 权限确认 — {self.title}", id="confirm-title")
            yield Static(f"操作: {self.detail}", id="confirm-command")
            yield Static(self._render_options(), id="confirm-options")
            yield Static(
                "↑↓ 选择  Enter 确认  1-4 直达  Esc 拒绝 · "
                "[dim]F2 可切换放行模式[/dim]",
                id="confirm-hint",
            )

    def on_mount(self) -> None:
        self.set_timer(0.3, self._activate)

    def _activate(self) -> None:
        self._active = True

    def _render_options(self) -> str:
        lines = []
        for i, (num, label) in enumerate(self.OPTIONS):
            if i == self._cursor:
                lines.append(f"[bold reverse] ❯ {num}. {label} [/bold reverse]")
            else:
                lines.append(f"   {num}. {label}")
        return "\n".join(lines)

    def _move(self, delta: int) -> None:
        self._cursor = (self._cursor + delta) % len(self.OPTIONS)
        self.query_one("#confirm-options", Static).update(self._render_options())

    def _select(self, index: int) -> None:
        self.dismiss(self.CHOICES[index])

    def on_key(self, event) -> None:
        if not self._active:
            return
        key = event.key
        if key == "up":
            event.stop()
            self._move(-1)
        elif key == "down":
            event.stop()
            self._move(1)
        elif key == "enter":
            event.stop()
            self._select(self._cursor)
        elif key in ("1", "2", "3", "4"):
            event.stop()
            self._select(int(key) - 1)
        elif key == "escape":
            event.stop()
            self.dismiss(HumanChoice.DENY)