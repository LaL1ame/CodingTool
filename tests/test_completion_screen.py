"""补齐屏(headless,用 textual run_test) —— 复现 'str has no render_strips' 渲染崩溃。"""
from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import ListView, Static

from codepilot.ui.screens.completion_screen import CompletionScreen

_THEME = (Path(__file__).resolve().parent.parent /
          "codepilot" / "ui" / "theme.tcss").read_text(encoding="utf-8")


class Host(App):
    CSS = _THEME   # 加载真实主题,使补全/确认屏的 Static 正常渲染(避免机型崩溃)

    def compose(self) -> ComposeResult:
        yield Static("placeholder")


async def test_completion_screen_renders_with_theme():
    app = Host()
    async with app.run_test():
        picked = []
        screen = CompletionScreen(["plan", "do", "clear", "status"], on_pick=picked.append)
        await app.push_screen(screen)   # 挂载即渲染,不应抛 'str has no render_strips'
        assert screen._candidates == ["plan", "do", "clear", "status"]
        assert len(screen.query(ListView).first().children) == 4