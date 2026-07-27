"""Provider 选择屏幕 —— 启动时的多配置选择界面。"""

from textual import on
from textual.app import ComposeResult
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import ListItem, ListView, Static

from mewcode.config import ProviderConfig


class ProviderSelectScreen(ModalScreen[ProviderConfig]):
    """多 Provider 选择屏幕。

    方向键导航列表，Enter 确认，返回选中的 ProviderConfig。
    """

    BINDINGS = [
        ("enter", "select", "确认"),
        ("escape", "quit", "退出"),
    ]

    def __init__(self, configs: list[ProviderConfig]) -> None:
        super().__init__()
        self.configs = configs

    def compose(self) -> ComposeResult:
        with Container(id="select-container"):
            yield Static(
                "[bold]Select a Provider[/bold]\n"
                "[dim]Use ↑↓ to navigate, Enter to confirm[/dim]",
                id="select-title",
            )
            yield ListView(
                *[
                    ListItem(
                        Static(
                            f"[bold]{c.name}[/bold]\n"
                            f"  {c.protocol}  |  {c.model}"
                        )
                    )
                    for c in self.configs
                ],
                id="provider-list",
            )

    def on_mount(self) -> None:
        self.query_one("#provider-list", ListView).focus()

    @on(ListView.Selected)
    def on_selected(self, event: ListView.Selected) -> None:
        idx = event.list_view.index
        if 0 <= idx < len(self.configs):
            self.dismiss(self.configs[idx])

    def action_quit(self) -> None:
        self.dismiss(None)
