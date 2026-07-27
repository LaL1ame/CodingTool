"""MewCode TUI 主 App —— Textual 驱动的全功能终端界面。"""

import asyncio
import time

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Container

from mewcode.chat.manager import ChatManager
from mewcode.config import ProviderConfig
from mewcode.providers.base import BaseProvider
from mewcode.tui.widgets.banner import BannerWidget
from mewcode.tui.widgets.chat_area import ChatArea
from mewcode.tui.widgets.input_box import InputBox
from mewcode.tui.widgets.provider_select import ProviderSelectScreen
from mewcode.tui.widgets.status_bar import StatusBar

CSS = """
Screen {
    layout: vertical;
    background: $surface;
}

#banner-container {
    dock: top;
    height: auto;
    padding: 0 1;
    border-bottom: solid $primary;
    background: $panel;
}

#chat-area {
    height: 1fr;
    padding: 0 1;
}

#input-container {
    dock: bottom;
    height: auto;
    padding: 0 1;
    border-top: solid $primary;
}

#status-bar {
    dock: bottom;
    height: 1;
    padding: 0 2;
    background: $primary-darken-2;
    color: $text;
}

/* Provider selection screen */
#select-container {
    width: 50;
    height: auto;
    max-height: 20;
    padding: 2;
    border: thick $primary;
    background: $panel;
}

#select-title {
    padding-bottom: 1;
    border-bottom: solid $primary-darken-1;
}

#provider-list {
    padding-top: 1;
    height: auto;
}
"""


class MewCodeApp(App):
    """MewCode 主应用程序。"""

    CSS = CSS
    BINDINGS = [
        ("ctrl+c", "quit", "退出"),
    ]

    def __init__(self, configs: list[ProviderConfig]) -> None:
        super().__init__()
        self.configs = configs
        self.provider: BaseProvider | None = None
        self.chat_manager: ChatManager | None = None
        self._timer_task: asyncio.Task | None = None
        self._stream_start: float = 0.0

    # ---- Lifecycle ---------------------------------------------------------

    def on_mount(self) -> None:
        """启动后选择 provider（如有多项）或直接初始化。"""
        if len(self.configs) == 0:
            self.exit(message="[red]No providers configured[/red]")
            return

        if len(self.configs) == 1:
            self._init_with_config(self.configs[0])
        else:
            self.push_screen(
                ProviderSelectScreen(self.configs),
                callback=self._on_provider_selected,
            )

    def _on_provider_selected(self, config: ProviderConfig | None) -> None:
        """Provider 选择回调。"""
        if config is None:
            self.exit()
            return
        self._init_with_config(config)

    def _init_with_config(self, config: ProviderConfig) -> None:
        """根据选定配置初始化 Provider 和 ChatManager。"""
        self.provider = BaseProvider.create(config)
        self.chat_manager = ChatManager(self.provider)

        # 更新状态栏
        sb = self.query_one("#status-bar", StatusBar)
        sb.provider_name = config.name
        sb.model_name = config.model
        sb.refresh_display()

        # 聚焦输入框
        self.query_one("#message-input", InputBox).focus()

    # ---- Compose -----------------------------------------------------------

    def compose(self) -> ComposeResult:
        """构建界面布局。"""
        with Container(id="banner-container"):
            yield BannerWidget()
        yield ChatArea(id="chat-area")
        with Container(id="input-container"):
            yield InputBox(id="message-input")
        yield StatusBar(
            provider_name="",
            model_name="",
            id="status-bar",
        )

    # ---- Message Handler ---------------------------------------------------

    @work(exclusive=True)
    async def on_input_box_submitted(self, event: InputBox.Submitted) -> None:
        """处理用户提交的消息。"""
        text = event.text.strip()

        if not self.chat_manager:
            return

        # /exit 命令
        if text == "/exit":
            self.exit()
            return

        # ---- UI 准备 ----
        chat_area = self.query_one("#chat-area", ChatArea)
        status_bar = self.query_one("#status-bar", StatusBar)
        input_box = self.query_one("#message-input", InputBox)

        # 锁定输入
        input_box.lock()

        # 显示用户消息
        chat_area.add_user_message(text)

        # 开始流式助手消息
        chat_area.start_assistant_stream()

        # 启动计时器
        self._stream_start = time.time()
        self.set_interval(1, self._update_timer)

        try:
            async for delta in self.chat_manager.send_message(text):
                if delta.error:
                    elapsed = time.time() - self._stream_start
                    chat_area.show_error(delta.error)
                    self._stop_timer()
                    status_bar.refresh_display(f"[red]Error ({elapsed:.1f}s)[/red]")
                    input_box.unlock()
                    return

                if delta.text:
                    chat_area.append_stream_text(delta.text)

                if delta.done:
                    elapsed = time.time() - self._stream_start
                    chat_area.finalize_assistant_message()
                    self._stop_timer()
                    status_bar.refresh_display(f"[green]✓ {elapsed:.1f}s[/green]")
                    input_box.unlock()
                    return

        except Exception as e:
            elapsed = time.time() - self._stream_start
            chat_area.show_error(str(e))
            self._stop_timer()
            status_bar.refresh_display(f"[red]Error ({elapsed:.1f}s)[/red]")
            input_box.unlock()

    # ---- Timer -------------------------------------------------------------

    def _update_timer(self) -> None:
        """每秒更新状态栏计时显示。"""
        if self._stream_start > 0:
            elapsed = time.time() - self._stream_start
            self.query_one("#status-bar", StatusBar).refresh_display(
                f"[bold yellow]Imagining… ({elapsed:.0f}s)[/bold yellow]"
            )

    def _stop_timer(self) -> None:
        """停止所有定时器。"""
        self._stream_start = 0.0
        for timer in list(self._timers):
            timer.stop()
