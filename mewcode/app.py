"""TUI 层：Textual App。"""

import os
import time

from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, Input, RichLog, Static

from mewcode.chat import ChatManager
from mewcode.config import ProviderConfig
from mewcode.providers import BaseProvider

CAT = r"""
  ╱|、
 (˚ˎ 。7
  |、˜〵
  じしˍ,)ノ"""

VERSION = "0.1.0"


class MewCodeApp(App):
    """MewCode 主 TUI — 原生 Textual 组件。"""

    BINDINGS = [("ctrl+c", "quit", "退出")]

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__()
        self._config = config
        self._provider: BaseProvider | None = None
        self._chat: ChatManager | None = None
        self._streaming = False
        self._stream_start: float = 0.0

    def on_mount(self) -> None:
        self._provider = BaseProvider.create(self._config)
        self._chat = ChatManager(self._provider)

        cwd = os.getcwd()
        if len(cwd) > 50:
            cwd = "..." + cwd[-47:]

        log = self.query_one("#chat", RichLog)
        log.write(f"[bold cyan]{CAT}[/bold cyan]\n")
        log.write(f"[bold]MewCode[/bold] v{VERSION}  |  {cwd}\n")
        log.write(f"[dim]{self._config.name} · {self._config.model} — Ready[/dim]\n")
        self._update_status()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield RichLog(id="chat", highlight=True, markup=True, wrap=True)
        yield Input(placeholder="❯ Send a message...  (/exit to quit)", id="prompt")
        yield Footer()

    # ---- 消息处理 -----------------------------------------------------------

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text or not self._chat or self._streaming:
            return
        if text == "/exit":
            self.exit()
            return

        log = self.query_one("#chat", RichLog)
        inp = self.query_one("#prompt", Input)

        # 立即清空输入框 + 锁定
        inp.value = ""
        inp.disabled = True

        log.write(f"\n[bold green]❯ You[/bold green]  {text}")
        log.write("[bold]🤖 MewCode[/bold] ")

        # 流式输出区 — 只 mount 一次，之后仅 update()
        stream = Static("", id="stream")
        log.mount(stream)

        self._streaming = True
        self._stream_start = time.time()
        self.set_interval(1, self._tick)

        buf: list[str] = []
        try:
            async for d in self._chat.send_message(text):
                if d.error:
                    stream.remove()
                    log.write(f"\n[bold red]❌ Error[/bold red]  {d.error}")
                    break
                if d.text:
                    buf.append(d.text)
                    stream.update("".join(buf))
                if d.done:
                    stream.remove()
                    # 整段写入 RichLog，不拆段
                    log.write("\n" + "".join(buf))
                    elapsed = time.time() - self._stream_start
                    log.write(f"\n[dim]── {elapsed:.1f}s[/dim]")
                    break
        except Exception as e:
            stream.remove()
            log.write(f"\n[bold red]❌ Error[/bold red]  {e}")
        finally:
            self._stop_timer()
            inp.disabled = False
            self._streaming = False

    # ---- 状态栏 & 计时器 -----------------------------------------------------

    def _update_status(self, extra: str = "") -> None:
        self.sub_title = f"{self._config.name}  |  {self._config.model}{extra}"

    def _tick(self) -> None:
        if self._stream_start > 0:
            elapsed = time.time() - self._stream_start
            self._update_status(f"  |  Imagining… ({elapsed:.0f}s)")

    def _stop_timer(self) -> None:
        self._stream_start = 0.0
        for t in list(self._timers):
            t.stop()
        self._update_status()
