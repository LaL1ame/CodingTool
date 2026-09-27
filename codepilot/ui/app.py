"""CodePilot 主 TUI App(编排层)。

承接旧 codepilot/app.py 的编排逻辑,迁入 ui/ 包:
- 用 StatusBar 底部状态栏(tcss dock),Header 的 sub_title 退居次要。
- 工具调用以 ToolRow 追加进会话滚动区(不再每轮清空)。
- 修正 token 为会话级累计。
模式状态机(T2)、就地流式(T4后续)、权限屏已引入。
"""
from __future__ import annotations

import os
import time

from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Footer, Header, Input, RichLog, Static

from codepilot.agent import Agent
from codepilot.chat import ChatManager
from codepilot.commands import parse_command
from codepilot.modes import ModeState, ReleasePolicy, ToolAxis
from codepilot.permissions.mode import PermissionMode
from codepilot.permissions.policy import HumanChoice
from codepilot.ui.widgets import StatusBar, ToolRow

CAT = r"""
  ╱|、
 (˚ˎ 。7
  |、˜〵
  じしˍ,)ノ"""

VERSION = "0.2.0"


class CodePilotApp(App):
    BINDINGS = [("ctrl+c", "quit", "退出"), ("escape", "cancel_loop", "取消"),
                ("shift+tab", "cycle_axis", "切模式")]

    CSS_PATH = "theme.tcss"

    def __init__(self, agent: Agent, release: str = "manual",
             history: list | None = None, session_file: str | None = None,
             initial_modes: dict | None = None) -> None:
        super().__init__()
        self._agent = agent
        self._mode_state = ModeState(release=ReleasePolicy(release))
        if initial_modes:
            self._mode_state.restore(initial_modes)
        self._history = history
        self._session_file = session_file
        self._chat: ChatManager | None = None
        self._streaming = False
        self._stream_start: float = 0.0
        self._tool_rows: dict[str, object] = {}
        # 会话级累计 token(修正:单轮覆盖的问题)
        self._tokens_in: int = 0
        self._tokens_out: int = 0
        self._status_round: str = ""

    def on_mount(self) -> None:
        self._chat = ChatManager(self._agent, history=self._history,
                                 session_file=self._session_file)

        cwd = os.getcwd()
        if len(cwd) > 50:
            cwd = "..." + cwd[-47:]

        cfg = self._agent._provider.config
        proto = cfg.protocol.upper()

        log = self.query_one("#chat", RichLog)
        log.styles.height = "1fr"

        log.write(f"[bold cyan]{CAT}[/bold cyan]\n")
        log.write(f"[bold]CodePilot[/bold] v{VERSION}  |  {cwd}\n")
        log.write(f"[dim]{cfg.name} · {cfg.model}  ({proto}) — Ready[/dim]\n")

        self._apply_modes()  # 把构造传入的初始 release 同步到后端
        self._refresh_status()

    def on_unmount(self) -> None:
        """退出时把最终模式快照写入会话文件,供下次 resume。"""
        if self._session_file:
            from codepilot.session import store
            store.append_mode(self._session_file, self._mode_state.snapshot())

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield RichLog(id="chat", highlight=True, markup=True, wrap=True)
        yield Vertical(id="tools")      # 工具 gutter:持久追加
        yield Static("", id="stream")
        yield Input(placeholder="❯ Send a message...  (/exit /plan /do)", id="prompt")
        yield Footer()
        yield StatusBar()

    def action_cancel_loop(self) -> None:
        if self._streaming:
            self._agent.cancel()

    def action_cycle_axis(self) -> None:
        """Shift+Tab:Plan ⇄ Build 工具轴切换。"""
        self._set_axis(self._mode_state.tool_axis.cycle_tool_axis(), announce=True)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text or not self._chat or self._streaming:
            return

        command, rest = parse_command(text)

        # —— 纯模式/开关命令:不进入聊天循环 ——
        if command == "exit":
            self.exit()
            return
        if command in ("mode", "auto", "manual", "strict"):
            self._cmd_release(command, rest)
            return
        if command == "edits":
            self._cmd_edits(rest)
            return
        if command == "plan":
            self._set_axis(ToolAxis.PLAN, announce=True)
            if not rest:
                return
            text = rest
        elif command == "do":
            self._set_axis(ToolAxis.BUILD, announce=True)
            if not rest:
                return
            text = rest

        inp = self.query_one("#prompt", Input)
        log = self.query_one("#chat", RichLog)

        inp.value = ""
        inp.disabled = True

        log.write(f"\n[bold green]❯ You[/bold green]  {text}")
        log.write("[bold]🤖 CodePilot[/bold] ")

        self._streaming = True
        self._stream_start = time.time()
        self.set_interval(1, self._tick)
        self.run_worker(self._process_chat(text), exclusive=False)

    # —— 模式命令处理 ——
    def _apply_modes(self) -> None:
        """把 ModeState 翻译到后端:Agent 工具轴 + PermissionManager 放行/自动编辑。"""
        self._agent.set_plan_mode(self._mode_state.tool_axis is ToolAxis.PLAN)
        perm = getattr(self._agent, "_permission", None)
        if perm is not None:
            perm.set_mode(PermissionMode[self._mode_state.release_permission_name()])
            perm.set_accept_edits(self._mode_state.accept_edits)
        self._refresh_status()

    def _set_axis(self, axis: ToolAxis, announce: bool = False) -> None:
        self._mode_state.set_axis(axis)
        self._apply_modes()
        label = "计划模式（只读工具）" if axis is ToolAxis.PLAN else "执行模式（全部工具）"
        if announce:
            self.query_one("#chat", RichLog).write(f"\n[dim]── 已切换：{label}[/dim]")
        self.query_one("#prompt", Input).value = ""

    def _cmd_release(self, command: str, rest: str) -> None:
        if command == "mode":
            name = (rest or "").strip().lower()
            if name not in ("manual", "auto", "strict"):
                self.query_one("#chat", RichLog).write(
                    "\n[bold red]⚠ /mode 需 manual|auto|strict[/bold red]")
                return
        else:
            name = command  # /manual /auto /strict
        policy = ReleasePolicy(name)
        self._mode_state.set_release(policy)
        self._apply_modes()
        self.query_one("#prompt", Input).value = ""
        self.query_one("#chat", RichLog).write(
            f"\n[dim]── 放行策略 → {name}{'（未命中自动放行）' if name == 'auto' else ''}[/dim]")

    def _cmd_edits(self, rest: str) -> None:
        arg = (rest or "").strip().lower()
        if arg in ("on", "off"):
            self._mode_state.toggle_accept_edits(arg == "on")
        else:
            self._mode_state.toggle_accept_edits()  # 无参 → 翻转
        on = self._mode_state.accept_edits
        self._apply_modes()
        self.query_one("#prompt", Input).value = ""
        self.query_one("#chat", RichLog).write(
            f"\n[dim]── Accept Edits → {'ON(自动应用 write/edit)' if on else 'OFF'}[/dim]")

    async def _process_chat(self, text: str) -> None:
        log = self.query_one("#chat", RichLog)
        inp = self.query_one("#prompt", Input)
        stream = self.query_one("#stream", Static)
        tools_container = self.query_one("#tools", Vertical)
        stream.update("")
        tools_container.remove_children()  # 瞬时面板按轮清空(已定型的历史写入 chat 滚动区)
        self._tool_rows.clear()

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

                if d.usage is not None and d.usage.input_tokens:
                    self._tokens_in += int(d.usage.input_tokens)
                    self._tokens_out += int(d.usage.output_tokens)
                    self._refresh_status()

                if d.tool_calls:
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
                    self._flush_tool_rows(log, tools_container)
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
            self._tool_rows.clear()
            self._stop_timer()
            inp.disabled = False
            self._streaming = False

    def _flush_tool_rows(self, log: RichLog, tools_container: Vertical) -> None:
        """把本轮的已定型工具行写入可滚动会话区,再清空瞬时面板。

        使工具历史随对话滚动(可回溯),不占固定屏幕高度 —— 对齐 Claude Code。
        """
        if self._tool_rows:
            log.write("\n")
            for row in list(self._tool_rows.values()):
                log.write(row.line())
        try:
            tools_container.remove_children()
        except Exception:
            pass
        self._tool_rows.clear()

    async def ask_permission(self, tool: str, subject: str) -> HumanChoice:
        from codepilot.ui.screens import PermissionScreen
        detail = f"{tool}({subject[:80]})" if subject else tool
        screen = PermissionScreen(tool, detail)
        result = await self.push_screen(screen, wait_for_dismiss=True)
        return result if isinstance(result, HumanChoice) else HumanChoice.DENY

    # —— 状态栏 ——
    def _mode_summary(self) -> tuple[str, str, bool]:
        """(axis, release, accept_edits) —— 从 ModeState 读取。"""
        return self._mode_state.tool_axis.value, self._mode_state.release.value, self._mode_state.accept_edits

    def _refresh_status(self) -> None:
        cfg = self._agent._provider.config
        axis, release, ae = self._mode_summary()
        sb = self.query_one(StatusBar)
        sb.update_state(
            axis=axis, release=release, accept_edits=ae, model=cfg.model,
            tokens=self._tokens_in + self._tokens_out,
            elapsed_s=max(0.0, time.time() - self._stream_start) if self._stream_start else 0.0,
        )
        self.sub_title = f"{cfg.name}  |  {cfg.model}  {self._status_round}"

    def _tick(self) -> None:
        if self._stream_start > 0:
            self._refresh_status()

    def _stop_timer(self) -> None:
        self._stream_start = 0.0
        self._status_round = ""
        for t in list(self._timers):
            t.stop()
        self._refresh_status()

    # —— 其它辅助(原样) ——
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