"""TUI 层：Textual App — 含工具调用展示和命令确认屏幕。"""

import os
import time

from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Input, RichLog, Static

from codepilot.agent import Agent
from codepilot.chat import ChatManager

CAT = r"""
  ╱|、
 (˚ˎ 。7
  |、˜〵
  じしˍ,)ノ"""

VERSION = "0.2.0"


# ── 命令确认屏幕（Claude Code 风格） ──────────────────────────

class ConfirmScreen(Screen[bool]):
    """返回 True(批准) / False(拒绝)。第三个选项"始终允许"等价于批准。"""

    OPTIONS = [
        ("1", "批准执行"),
        ("2", "拒绝执行"),
        ("3", "始终允许（本次会话）"),
    ]

    def __init__(self, command: str) -> None:
        super().__init__()
        self.command = command
        self._cursor = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Static("⚠ 确认执行命令", id="confirm-title")
            yield Static(f"[bold reverse]{self.command}[/bold reverse]", id="confirm-command")
            yield Static(self._render_options(), id="confirm-options")
            yield Static("[dim]↑↓ 选择  Enter 确认  1/2/3 直达  Esc 拒绝[/dim]", id="confirm-hint")

    def on_mount(self) -> None:
        box = self.query_one("#confirm-box")
        box.styles.border = ("heavy", "cyan")
        box.styles.padding = (1, 2)
        self._active = False
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
        self.query_one("#confirm-options").update(self._render_options())

    def _select(self, index: int) -> None:
        # 选项 0 (批准) / 2 (始终允许) → True, 选项 1 (拒绝) → False
        self.dismiss(index != 1)

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
        elif key in ("1", "2", "3"):
            event.stop()
            self._select(int(key) - 1)
        elif key == "escape":
            event.stop()
            self.dismiss(False)


# ── 工具行 Widget ─────────────────────────────────────────────

class ToolRow(Static):
    def __init__(self, tool_id: str, tool_name: str, params_summary: str) -> None:
        super().__init__("", id=f"tool-{tool_id}")
        self.tool_id = tool_id
        self.tool_name = tool_name
        self.set_pending(params_summary)

    def set_pending(self, params_summary: str) -> None:
        self.update(f"[dim]●[/dim] [bold]{self.tool_name}[/bold]({params_summary})")

    def set_done(self, summary: str) -> None:
        self.update(f"[dim]●[/dim] [bold]{self.tool_name}[/bold]([green]{summary}[/green])")

    def set_error(self, message: str) -> None:
        short = message[:80] + "..." if len(message) > 80 else message
        self.update(f"[red]✕[/red] [bold]{self.tool_name}[/bold]: [red]{short}[/red]")


# ── 主 App ────────────────────────────────────────────────────

class CodePilotApp(App):
    BINDINGS = [("ctrl+c", "quit", "退出")]

    def __init__(self, agent: Agent) -> None:
        super().__init__()
        self._agent = agent
        self._chat: ChatManager | None = None
        self._streaming = False
        self._stream_start: float = 0.0
        self._tool_rows: dict[str, ToolRow] = {}
        self._always_allow: bool = False

    def on_mount(self) -> None:
        self._chat = ChatManager(self._agent)

        cwd = os.getcwd()
        if len(cwd) > 50:
            cwd = "..." + cwd[-47:]

        cfg = self._agent._provider.config
        proto = cfg.protocol.upper()

        log = self.query_one("#chat", RichLog)
        log.write(f"[bold cyan]{CAT}[/bold cyan]\n")
        log.write(f"[bold]CodePilot[/bold] v{VERSION}  |  {cwd}\n")
        log.write(f"[dim]{cfg.name} · {cfg.model}  ({proto}) — Ready[/dim]\n")
        self._update_status()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield RichLog(id="chat", highlight=True, markup=True, wrap=True)
        yield Static("", id="stream")
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

        inp = self.query_one("#prompt", Input)
        log = self.query_one("#chat", RichLog)

        inp.value = ""
        inp.disabled = True

        log.write(f"\n[bold green]❯ You[/bold green]  {text}")
        log.write("[bold]🤖 CodePilot[/bold] ")

        # 整个对话循环放进 Worker，不阻塞 Textual 消息泵
        self._streaming = True
        self._stream_start = time.time()
        self.set_interval(1, self._tick)
        self.run_worker(self._process_chat(text), exclusive=False)

    async def _process_chat(self, text: str) -> None:
        """Worker: 运行整个对话循环（含可能的工具调用 + 确认交互）。"""
        log = self.query_one("#chat", RichLog)
        inp = self.query_one("#prompt", Input)
        stream = self.query_one("#stream", Static)
        stream.update("")

        buf: list[str] = []
        try:
            async for d in self._chat.send_message(text):
                if d.error:
                    stream.update("")
                    log.write(f"\n[bold red]❌ Error[/bold red]  {d.error}")
                    return

                if d.tool_calls:
                    for tc in d.tool_calls:
                        params = self._format_params(tc.arguments)
                        row = ToolRow(tc.id, tc.name, params)
                        self._tool_rows[tc.id] = row
                        log.mount(row)

                elif d.tool_result:
                    tr = d.tool_result
                    row = self._tool_rows.get(tr.call_id)
                    if row:
                        if tr.error:
                            row.set_error(tr.error)
                        else:
                            row.set_done(self._format_result(tr))
                    else:
                        if tr.error:
                            log.write(f"\n[red]✕ {tr.name}[/red]: {tr.error}")
                        else:
                            log.write(f"\n[dim]● {tr.name}: {self._format_result(tr)}[/dim]")

                elif d.text:
                    buf.append(d.text)
                    stream.update("".join(buf))

                if d.done:
                    stream.update("")
                    log.write("\n" + "".join(buf))
                    elapsed = time.time() - self._stream_start
                    log.write(f"\n[dim]── {elapsed:.1f}s[/dim]")
                    return

        except Exception as e:
            log.write(f"\n[bold red]❌ Error[/bold red]  {e}")
        finally:
            try:
                stream.update("")
            except Exception:
                pass
            self._stop_timer()
            self._tool_rows.clear()
            inp.disabled = False
            self._streaming = False

    # ---- 命令确认（在 Worker 中调用，不阻塞消息泵）------------------

    async def confirm_command(self, command: str) -> bool:
        """Worker 中调用 push_screen + wait_for_dismiss，不阻塞消息泵。"""
        if self._always_allow:
            return True

        screen = ConfirmScreen(command)
        # wait_for_dismiss=True 返回 Future，Worker 中 await 不阻塞消息泵
        result = await self.push_screen(screen, wait_for_dismiss=True)

        # "始终允许" 选项: _select(2) → dismiss(True)，与批准相同
        # 我们需要区分"批准"和"始终允许"——用 cursor 位置判断
        if screen._cursor == 2:  # 选了"始终允许"
            self._always_allow = True

        return result is True

    # ---- 辅助方法 -----------------------------------------------------------

    @staticmethod
    def _format_params(arguments: dict) -> str:
        parts = []
        for v in arguments.values():
            s = str(v)
            if len(s) > 40:
                s = s[:37] + "..."
            parts.append(s)
        return ", ".join(parts)

    @staticmethod
    def _format_result(result) -> str:
        if result.truncated and result.total_items:
            return f"{result.total_items} items (truncated)"
        output = result.output or ""
        lines = output.split("\n")
        if len(lines) == 1 and len(output) <= 60:
            return output
        return f"{len(lines)} lines"

    # ---- 状态栏 & 计时器 -----------------------------------------------------

    def _update_status(self, extra: str = "") -> None:
        cfg = self._agent._provider.config
        self.sub_title = f"{cfg.name}  |  {cfg.model}{extra}"

    def _tick(self) -> None:
        if self._stream_start > 0:
            elapsed = time.time() - self._stream_start
            self._update_status(f"  |  Imagining… ({elapsed:.0f}s)")

    def _stop_timer(self) -> None:
        self._stream_start = 0.0
        for t in list(self._timers):
            t.stop()
        self._update_status()
