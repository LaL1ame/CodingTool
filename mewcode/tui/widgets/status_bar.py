"""底部状态栏 —— 显示 provider 名称、模型名、计时文本。"""

from textual.widgets import Static


class StatusBar(Static):
    """底部状态栏。

    左侧：provider 名称
    右侧：模型名
    流式期间显示计时文本。
    """

    def __init__(self, provider_name: str = "", model_name: str = "") -> None:
        super().__init__("")
        self.provider_name = provider_name
        self.model_name = model_name

    def on_mount(self) -> None:
        self.refresh_display()

    def refresh_display(self, timer_text: str | None = None) -> None:
        """刷新状态栏显示。

        Args:
            timer_text: 计时文本，如 "Imagining… (5s)"。
                       为 None 时显示默认 "Ready" 状态。
        """
        left = f"[bold]{self.provider_name}[/bold]"
        right = self.model_name
        center = timer_text if timer_text else "Ready"
        display = f"{left}  {center}  {right}"
        self.update(display)
