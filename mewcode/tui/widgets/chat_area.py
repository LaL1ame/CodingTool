"""对话区 —— 消息列表、流式更新、Markdown 渲染。"""

from textual.containers import VerticalScroll
from textual.widgets import Markdown, Static


class ChatArea(VerticalScroll):
    """可滚动的对话区。

    用户消息以 "❯ You" 前缀显示，
    助手消息流式期间逐字追加，结束后切换为 Markdown 渲染。
    错误消息以区分样式显示。
    """

    def __init__(self) -> None:
        super().__init__()
        self._streaming_widget: Static | None = None
        self._streaming_text: list[str] = []

    # ---- 用户消息 -----------------------------------------------------------

    def add_user_message(self, text: str) -> None:
        """添加用户消息到对话区。"""
        self.mount(Static(f"[bold]❯ You[/bold]\n{text}", classes="user-msg"))
        self.scroll_end()

    # ---- 助手消息（流式）----------------------------------------------------

    def start_assistant_stream(self) -> None:
        """开始一个流式助手消息块。"""
        self._streaming_text = []
        self._streaming_widget = Static("", classes="assistant-msg-streaming")
        self.mount(self._streaming_widget)

    def append_stream_text(self, text: str) -> None:
        """追加文本到当前流式消息块。"""
        self._streaming_text.append(text)
        if self._streaming_widget is not None:
            display = "".join(self._streaming_text)
            self._streaming_widget.update(f"[bold]🤖 MewCode[/bold]\n{display}")
        self.scroll_end()

    def finalize_assistant_message(self) -> str:
        """结束流式，替换为 Markdown 渲染。

        Returns:
            完整的回复文本。
        """
        full_text = "".join(self._streaming_text)
        if self._streaming_widget is not None:
            self._streaming_widget.remove()
            self._streaming_widget = None
        self._streaming_text = []
        self.mount(Markdown(f"### 🤖 MewCode\n\n{full_text}"))
        self.scroll_end()
        return full_text

    # ---- 错误消息 -----------------------------------------------------------

    def show_error(self, error_text: str) -> None:
        """以区分样式显示错误信息。"""
        if self._streaming_widget is not None:
            self._streaming_widget.remove()
            self._streaming_widget = None
        self._streaming_text = []
        self.mount(
            Static(
                f"[bold red]❌ Error[/bold red]\n[red dim]{error_text}[/red dim]",
                classes="error-msg",
            )
        )
        self.scroll_end()
