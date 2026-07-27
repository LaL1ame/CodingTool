"""底部状态栏 —— 显示 provider 名称、模型名、计时文本。"""

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static


class StatusBar(Horizontal):
    """底部状态栏。

    左侧：provider 名称
    中间：计时/状态文本
    右侧：模型名
    """

    def __init__(self, provider_name: str = "", model_name: str = "") -> None:
        super().__init__()
        self._provider_name = provider_name
        self._model_name = model_name

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @provider_name.setter
    def provider_name(self, value: str) -> None:
        self._provider_name = value

    @property
    def model_name(self) -> str:
        return self._model_name

    @model_name.setter
    def model_name(self, value: str) -> None:
        self._model_name = value

    def compose(self) -> ComposeResult:
        yield Static("", id="status-left")
        yield Static("", id="status-center")
        yield Static("", id="status-right")

    def on_mount(self) -> None:
        self.refresh_display()

    def refresh_display(self, timer_text: str | None = None) -> None:
        """刷新状态栏显示。

        Args:
            timer_text: 计时文本，如 "Imagining… (5s)"。
                       为 None 时显示默认 "Ready" 状态。
        """
        self.query_one("#status-left", Static).update(
            f"[bold]{self._provider_name}[/bold]"
        )
        self.query_one("#status-center", Static).update(
            timer_text if timer_text else "Ready"
        )
        self.query_one("#status-right", Static).update(
            f"[bold]{self._model_name}[/bold]"
        )
