"""TUI 层：Textual App — 含工具调用展示和命令确认屏幕。"""

import os
import time

from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Input, RichLog, Static

from codepilot.agent import Agent
from codepilot.chat import ChatManager
from codepilot.commands import parse_command
from codepilot.permissions.policy import HumanChoice

CAT = r"""
  ╱|、
 (˚ˎ 。7
  |、˜〵
  じしˍ,)ノ"""

VERSION = "0.2.0"


# ── 权限确认屏幕（人机回路） ─────────────────────────────────

class PermissionScreen(Screen[HumanChoice]):
    """五层防御第 5 层：规则未命中的 ask 交回用户。四选映射 HumanChoice。"""

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

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Static(f"⚠ 权限确认 — {self.title}", id="confirm-title")
            yield Static(f"[bold reverse]{self.detail}[/bold reverse]", id="confirm-command")
            yield Static(self._render_options(), id="confirm-options")
            yield Static("[dim]↑↓ 选择  Enter 确认  1-4 直达  Esc 拒绝[/dim]", id="confirm-hint")

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
    BINDINGS = [("ctrl+c", "quit", "退出"), ("escape", "cancel_loop", "取消")]

    def __init__(self, agent: Agent) -> None:
        super().__init__()
        self._agent = agent
        self._chat: ChatManager | None = None
        self._streaming = False
        self._stream_start: float = 0.0
        self._tool_rows: dict[str, ToolRow] = {}
        # 状态栏整合
        self._status_round: str = ""
        self._status_tokens: str = ""

    def on_mount(self) -> None:
        self._chat = ChatManager(self._agent)

        cwd = os.getcwd()
        if len(cwd) > 50:
            cwd = "..." + cwd[-47:]

        cfg = self._agent._provider.config
        proto = cfg.protocol.upper()

        log = self.query_one("#chat", RichLog)

        # —— 布局约束：RichLog 独占剩余高度，工具条/流式区按内容收缩，消除空黑块 ——
        log.styles.height = "1fr"
        tools_box = self.query_one("#tools", Vertical)
        tools_box.styles.height = "auto"
        tools_box.styles.max_height = 8
        stream_w = self.query_one("#stream", Static)
        stream_w.styles.height = "auto"
        stream_w.styles.max_height = 4
        tools_box.styles.border = ("round", "gray")  # 工具条加细边框，避免黑乎乎一片

        log.write(f"[bold cyan]{CAT}[/bold cyan]\n")
        log.write(f"[bold]CodePilot[/bold] v{VERSION}  |  {cwd}\n")
        log.write(f"[dim]{cfg.name} · {cfg.model}  ({proto}) — Ready[/dim]\n")
        self._update_status()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield RichLog(id="chat", highlight=True, markup=True, wrap=True)
        yield Vertical(id="tools")  # 工具行容器
        yield Static("", id="stream")
        yield Input(placeholder="❯ Send a message...  (/exit to quit)", id="prompt")
        yield Footer()

    def action_cancel_loop(self) -> None:
        """Esc：取消当前 Agent 循环。"""
        if self._streaming:
            self._agent.cancel()

    # ---- 消息处理 -----------------------------------------------------------

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text or not self._chat or self._streaming:
            return

        command, rest = parse_command(text)
        if command == "exit":
            self.exit()
            return
        if command == "plan":
            self._agent.set_plan_mode(True)
            self.query_one("#prompt", Input).value = ""
            self.query_one("#chat", RichLog).write("\n[dim]── 已切换：计划模式（只读工具）[/dim]")
            self._update_status("  |  Plan Mode")
            if not rest:
                return
            text = rest
        elif command == "do":
            self._agent.set_plan_mode(False)
            self.query_one("#prompt", Input).value = ""
            self.query_one("#chat", RichLog).write("\n[dim]── 已切换：执行模式（全部工具）[/dim]")
            self._update_status()
            if not rest:
                return
            text = rest

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
        tools_container = self.query_one("#tools", Vertical)
        stream.update("")
        tools_container.remove_children()  # 清空上一轮的工具行

        buf: list[str] = []
        try:
            async for d in self._chat.send_message(text):
                if d.error:
                    stream.update("")
                    log.write(f"\n[bold red]❌ Error[/bold red]  {d.error}")
                    return

                if d.cancelled:
                    stream.update("")
                    log.write("\n[dim]── 已取消[/dim]")
                    return

                if d.round is not None:
                    self._status_round = f"第 {d.round}/{d.max_rounds} 轮"
                    self._refresh_status()

                if d.usage is not None:
                    self._status_tokens = f"{d.usage.input_tokens}+{d.usage.output_tokens} tokens"
                    self._refresh_status()

                if d.tool_calls:
                    tools_container = self.query_one("#tools", Vertical)
                    for tc in d.tool_calls:
                        params = self._format_params(tc.arguments)
                        row = ToolRow(tc.id, tc.name, params)
                        self._tool_rows[tc.id] = row
                        tools_container.mount(row)

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
                tools_container.remove_children()
            except Exception:
                pass
            self._stop_timer()
            self._tool_rows.clear()
            inp.disabled = False
            self._streaming = False

    # ---- 权限人机回路（Worker 中调用，不阻塞消息泵）------------------

    async def ask_permission(self, tool: str, subject: str) -> HumanChoice:
        """权限 ask → 弹 PermissionScreen，返回 HumanChoice。"""
        detail = f"{tool}({subject[:80]})" if subject else tool
        screen = PermissionScreen(tool, detail)
        result = await self.push_screen(screen, wait_for_dismiss=True)
        return result if isinstance(result, HumanChoice) else HumanChoice.DENY

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

    def _refresh_status(self) -> None:
        """整合状态栏信息，避免多个源互相覆盖。"""
        parts = []
        if self._status_round:
            parts.append(self._status_round)
        if self._status_tokens:
            parts.append(self._status_tokens)
        extra = "  |  " + " | ".join(parts) if parts else ""
        self._update_status(extra)

    def _tick(self) -> None:
        if self._stream_start > 0:
            elapsed = time.time() - self._stream_start
            # 如果已有轮次/token 信息，保留它们；否则显示 Imagining
            if self._status_round or self._status_tokens:
                self._refresh_status()
            else:
                self._update_status(f"  |  Imagining… ({elapsed:.0f}s)")

    def _stop_timer(self) -> None:
        self._stream_start = 0.0
        self._status_round = ""
        self._status_tokens = ""
        for t in list(self._timers):
            t.stop()
        self._update_status()
