"""CodePilot 主 TUI App(编排层)。

承接旧 codepilot/app.py 的编排逻辑,迁入 ui/ 包:
- 用 StatusBar 底部状态栏(tcss dock),Header 的 sub_title 退居次要。
- 工具调用以 ToolRow 追加进会话滚动区(不再每轮清空)。
- 修正 token 为会话级累计。
模式状态机(T2)、就地流式(T4后续)、权限屏已引入。
"""
from __future__ import annotations

import inspect
import logging
import os
import time
from collections.abc import Callable

from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Footer, Header, RichLog, Static, TextArea

from codepilot.agent import Agent
from codepilot.chat import ChatManager
from codepilot.commands import (
    CommandContext,
    CommandKind,
    CommandSpec,
    StatusSnapshot,
    build_registry,
    dispatch_command,
    make_completer,
    parse_command,
)
from codepilot.modes import ModeState, ReleasePolicy, ToolAxis
from codepilot.providers import BaseProvider
from codepilot.skills import BUILTIN_DIR as _SKILL_BUILTIN_DIR
from codepilot.permissions.mode import PermissionMode
from codepilot.permissions.policy import HumanChoice
from codepilot.ui.widgets import PromptInput, StatusBar, ToolRow

_log = logging.getLogger("codepilot.ui.app")

CAT = r"""
  ╱|、
 (˚ˎ 。7
  |、˜〵
  じしˍ,)ノ"""

VERSION = "0.2.0"


class CodePilotApp(App):
    BINDINGS = [("ctrl+c", "quit", "退出"), ("escape", "cancel_loop", "取消"),
                ("f2", "cycle_release", "切换模式")]  # state11:Enter/Ctrl+Enter 由 PromptInput 内部接管

    CSS_PATH = "theme.tcss"

    def __init__(self, agent: Agent, release: str = "manual",
             history: list | None = None, session_file: str | None = None,
             initial_modes: dict | None = None, context_manager=None,
             seed_msgs: list | None = None,
             auto_memory: Callable[[list[dict]], None] | None = None,
             memory_injector=None,
             skill_manager=None,
             hook_engine=None,
             hook_injector=None) -> None:
        super().__init__()
        self._agent = agent
        self._context_manager = context_manager
        # state15: Skill 管理器(装配命令注册 + isolated 编排 + /clear 联动)。
        self._skill_manager = skill_manager
        # state16: Hook 引擎与注入器(session_start/end 发射 + notify + /clear 联动)。
        self._hook_engine = hook_engine
        self._hook_injector = hook_injector
        self._mode_state = ModeState(release=ReleasePolicy(release))
        if initial_modes:
            self._mode_state.restore(initial_modes)
        self._history = history
        self._seed_msgs = seed_msgs   # state12: 恢复回灌的完整 LLM 序列(含工具调用)
        self._session_file = session_file
        self._auto_memory = auto_memory       # state13: done 后记忆调度回调
        self._memory_injector = memory_injector  # state13: /memory 展示用
        # state14: 命令注册中心 + 界面控制接口 + Tab 补全(均框架无关,可注入/替换)
        self._command_registry = build_registry()
        # 核心内置命令名(不含 skill 命令):/reload 重注册时保护其不被 skill 覆盖
        self._core_names: frozenset[str] = frozenset(s.name for s in self._command_registry.all())
        self.command_ctx: CommandContext = _CommandContextImpl(self)
        self.completer = make_completer(self._command_registry)
        # state15: 把「加载 Skill 时」动态注册命令的回调接到本 App 注册中心;
        # 启动即注册内置 Skill 的命令(开箱即用)。
        if self._skill_manager is not None:
            self._skill_manager._on_register_command = self._register_skill_command
            self._register_builtin_skill_commands()
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
                                 session_file=self._session_file,
                                 msgs=self._seed_msgs,
                                 auto_memory=self._auto_memory,
                                 hooks=self._hook_engine)
        # 注入上下文管理器:绑定 chat + 注入 agent(层1拦截/usage锚点)
        if self._context_manager is not None:
            self._context_manager.attach_chat(self._chat)
            self._agent._context_manager = self._context_manager
            # state12:恢复回灌的完整序列超预算 → 先压一次,防重启恢复爆窗
            self.run_worker(self._context_manager.compact_if_over())

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
        self._replay_history(log)  # state12: 恢复会话时把上一段对话画回屏(可回滚,对齐 Claude Code)

        self._apply_modes()  # 把构造传入的初始 release 同步到后端
        self.query_one("#prompt", TextArea).focus()  # 启动即把输入焦点交给 prompt(否则落在 RichLog)
        self._refresh_status()

        # state16: session_start 发射 + notify 接线（app 就绪后才可写 RichLog）
        if self._hook_engine is not None:
            self._hook_engine.notify = self._hook_notify
            self.run_worker(self._hook_engine.fire(
                "session_start", {"resumed": bool(self._seed_msgs)}))

    def on_unmount(self) -> None:
        """退出时把最终模式快照写入会话文件,供下次 resume。"""
        if self._session_file:
            from codepilot.session import store
            store.append_mode(self._session_file, self._mode_state.snapshot())
        # state16: session_end best-effort 发射 + 后台任务有界收尾
        # （关停流程中异步动作可能被取消——记日志兜底，不保证 command 跑完）
        if self._hook_engine is not None:
            self.run_worker(self._hook_session_end())

    async def _hook_session_end(self) -> None:
        await self._hook_engine.fire("session_end", {})
        await self._hook_engine.aclose()

    def _hook_notify(self, msg: str) -> None:
        """Hook 的 TUI 系统行回调（引擎与 UI 解耦的唯一通道）。"""
        try:
            self.query_one("#chat", RichLog).write(f"\n[dim]{msg}[/dim]")
        except Exception as e:
            _log.warning("hook notify 写屏失败: %s", e)

    def _replay_history(self, log: RichLog) -> None:
        """state12:恢复会话时,把上一段对话整段画回屏幕,可往上回滚。

        只画 user/assistant 文本轮(与 _process_chat 新消息同款样式);
        全新会话(_history 为空)时不动。工具轮轨迹不回画(对齐「看到对话」的 MVP)。
        """
        if not self._history:
            return
        log.write("\n")
        for m in self._history:
            role = getattr(m, "role", None)
            content = str(getattr(m, "content", "") or "")
            if role == "user":
                log.write(f"[bold green]❯ You[/bold green]  {content}")
            elif role == "assistant":
                log.write("\n" + content)
        log.write("\n[dim]── 已恢复上一会话,上方为历史对话 ──[/dim]")

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield RichLog(id="chat", highlight=True, markup=True, wrap=True)
        yield Vertical(id="tools")      # 工具 gutter:持久追加
        yield Static("", id="stream")
        yield Static("Enter 发送 · Ctrl+J 换行 · 支持多行(可粘贴)  (/exit /plan /do)", id="prompt-hint")
        yield PromptInput(id="prompt", soft_wrap=True, completer=self.completer)  # soft_wrap:长行折行自适应;completer:Tab补全(state14)
        yield Footer()
        yield StatusBar()

    def action_cancel_loop(self) -> None:
        if self._streaming:
            self._agent.cancel()

    def action_cycle_release(self) -> None:
        """F2:Claude Code 式放行模式循环(MANUAL→AUTO→STRICT,同步 AE)。"""
        policy, ae = self._mode_state.cycle_release()
        self._apply_modes()
        ae_txt = " · 自动编辑 ON" if ae else ""
        self.query_one("#chat", RichLog).write(
            f"\n[dim]── 模式 → {policy.value}{ae_txt}[/dim]")
        self.query_one("#prompt", TextArea).clear()

    def on_prompt_input_completion_requested(self, msg) -> None:
        """Tab 多命中：弹补全菜单,选中后把 &lt;name&gt; 填回输入框(state14 F6)。"""
        from codepilot.ui.screens import CompletionScreen

        def on_pick(name: str) -> None:
            inp = self.query_one("#prompt", TextArea)
            if isinstance(inp, PromptInput):
                inp.apply_completion(name)

        self.push_screen(CompletionScreen(msg.candidates, on_pick))

    def on_prompt_input_submitted(self) -> None:
        """PromptInput 的 Submitted(PromptInput 内部 Enter 触发):读多行输入,清空,统一分发。"""
        if self._streaming or self._chat is None:
            return
        inp = self.query_one("#prompt", TextArea)
        text = (inp.text or "").strip()
        if not text:
            return
        inp.clear()
        self._submit(text)

    def _submit(self, text: str) -> None:
        """分流器(state14):命令→本地分发 / 非命令→AI。单一发送入口。

        四态:
          blank   → 早返回
          message → 交 AI(_start_ai)
          unknown → 给 /help 引导(不送 AI)
          command → dispatch_command;返回消息则转发 AI,否则本地处理完
        """
        pr = parse_command(text, self._command_registry)
        if pr.kind == "blank":
            return
        if pr.kind == "message":
            self._start_ai(pr.text)
            return
        if pr.kind == "unknown":
            self.query_one("#chat", RichLog).write(
                "\n[dim]未知命令。输入 [bold]/help[/bold] 查看可用命令[/dim]")
            return

        out = dispatch_command(pr.spec, self.command_ctx, pr.params)
        if out is None:
            return
        if inspect.isawaitable(out):
            # 协程处理器(N4):run_worker 执行,内部若产出消息再转发
            self.run_worker(self._finish_forward(out))
        else:
            self._start_ai(out)

    def _start_ai(self, text: str) -> None:
        """启动一轮对话:清输入、写 You、置流式、run_worker(_process_chat)。"""
        log = self.query_one("#chat", RichLog)
        inp = self.query_one("#prompt", TextArea)
        inp.disabled = True
        log.write(f"\n[bold green]❯ You[/bold green]  {text}")
        log.write("[bold]🤖 CodePilot[/bold] ")
        self._streaming = True
        self._stream_start = time.time()
        self.set_interval(1, self._tick)
        self.run_worker(self._process_chat(text), exclusive=False)

    async def _finish_forward(self, coro) -> None:
        """await 一个协程处理器;若经其产出消息则转发 AI。"""
        out = await coro
        if out:
            self._start_ai(out)

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
        self.query_one("#prompt", TextArea).clear()

    def _chat_log(self) -> RichLog:
        return self.query_one("#chat", RichLog)

    def _set_release(self, policy: ReleasePolicy) -> None:
        self._mode_state.set_release(policy)
        self._apply_modes()
        self.query_one("#prompt", TextArea).clear()
        tag = "（未命中自动放行）" if policy is ReleasePolicy.AUTO else ""
        self._chat_log().write(f"\n[dim]── 放行策略 → {policy.value}{tag}[/dim]")

    def _set_accept_edits(self, on: bool | None = None) -> None:
        self._mode_state.toggle_accept_edits(on)
        self._apply_modes()
        self.query_one("#prompt", TextArea).clear()
        on = self._mode_state.accept_edits
        self._chat_log().write(
            f"\n[dim]── Accept Edits → {'ON(自动应用 write/edit)' if on else 'OFF'}[/dim]")

    def _memory_report(self) -> str | None:
        """F6:/memory —— 分组展示当前注入的指令与记忆索引;未启用返回 None。"""
        if self._memory_injector is None:
            return None
        blocks = self._memory_injector.build()
        if not blocks:
            return None
        return "\n[bold cyan]── 当前记忆 ──[/bold cyan]\n" + "\n".join(blocks)

    def _delete_memory(self, keyword: str) -> int:
        return getattr(self._memory_injector, "store", None).delete(keyword) \
            if self._memory_injector is not None else 0

    def _clear_display(self) -> None:
        """/clear:清空屏显示(RichLog),保留会话历史与上下文。
        同时联动复位已激活的 Skill(激活态+白名单)与 Hook 注入块(state15/16),
        但保留已注册的 skill 命令。"""
        self.query_one("#chat", RichLog).clear()
        if self._skill_manager is not None:
            self._skill_manager.deactivate_all()
            self._refresh_status()
        if self._hook_injector is not None:
            self._hook_injector.clear()

    # —— state15: Skill 命令注册与 isolated 编排 ——
    def _register_skill_command(self, name: str, handler) -> None:
        """把 Skill 的命令注册进命令注册中心(加载/内置均走此,replace 覆盖热更新)。"""
        skill = self._skill_manager.require(name)
        spec = CommandSpec(
            name=skill.name,
            kind=CommandKind.LOCAL if skill.mode == "isolated" else CommandKind.UI,
            desc=skill.description,
            handler=handler,
            usage=f"/{skill.name} [参数]",
            arg_hint=skill.params_desc or None,
        )
        self._command_registry.register(spec, replace=True)

    def _register_builtin_skill_commands(self) -> None:
        """启动即注册内置 Skill 的命令(开箱即用);用户/项目 Skill 在 load 时注册。"""
        if self._skill_manager is None:
            return
        for name, skill in self._skill_manager.known.items():
            if skill.path is not None and skill.path.is_relative_to(_SKILL_BUILTIN_DIR):
                self._register_skill_command(name, self._skill_manager.command_handler(name))

    def _reload_skills(self) -> None:
        """/reload：会话内热重载 Skill——重扫三级更新 known 与命令,新写/改的 skill 立即可用(Claude Code 式)。"""
        if self._skill_manager is None:
            self._chat_log().write("\n[dim]── Skill 系统未启用[/dim]")
            return
        try:
            added, removed = self._skill_manager.reload()
        except Exception as e:
            _log.exception("skill reload failed")
            self._chat_log().write(f"\n[bold red]⚠ Skill 重载失败[/bold red]  {e}")
            return
        # 重注册全部已知 skill 命令;撞核心命令名则跳过,保护 /status 等不被覆盖。
        for name in sorted(self._skill_manager.known):
            if name in self._core_names:
                self._chat_log().write(
                    f"\n[dim]Skill「{name}」撞核心命令,不注册斜杠命令(仍可用 load_skill 加载)[/dim]")
                continue
            self._register_skill_command(name, self._skill_manager.command_handler(name))
        self._register_builtin_skill_commands()
        added_txt = "、".join(added) or "无"
        removed_txt = "、".join(removed) or "无"
        self._chat_log().write(
            f"\n[dim]── 已重载 Skill：新增 {len(added)}（{added_txt}）、移除 {len(removed)}（{removed_txt}）[/dim]")
        _log.info("skill reload: added=%s removed=%s", added, removed)

    async def _run_isolated_skill(self, skill, params: str) -> None:
        """独立模式:开一段独立 Agent 循环跑完,只把结论/摘要回流主对话。

        内部历史不污染主对话 _msgs;按 skill.bring_history 带入最近 N 条为背景;
        skill.model 可覆盖独立对话的模型(缺省沿用当前)。
        """
        log = self._chat_log()
        if self._chat is None:
            return
        skill_name = getattr(skill, "name", "skill")
        try:
            msgs = self._chat.build_context()
            bring = int(getattr(skill, "bring_history", 0) or 0)
            carried = list(msgs[-bring:]) if bring else []
            # 独立对话必须有一条明确的用户触发消息(空历史时尤其必需):否则 API 拒绝
            # 空 messages,且模型无「现在执行该 Skill」的指令。故始终补一条触发指令,
            # 再接待带入的历史(历史在前、触发在后)。
            trigger = (f"请执行 Skill「{skill_name}」的 SOP"
                       + (f"，附加要求：{params}" if params else "")
                       + "。遵循其完整指令完成并给出结论。")
            prompt_msgs = [*carried, {"role": "user", "content": trigger}]
            provider = getattr(self._agent, "_provider", None)
            want_model = getattr(skill, "model", None)
            if provider is not None and want_model \
                    and want_model != getattr(provider.config, "model", None):
                from dataclasses import replace as _replace
                provider = BaseProvider.create(_replace(provider.config, model=want_model))
            _log.info("isolated skill run: %s (bring=%d, msgs=%d, model=%s)",
                      skill_name, bring, len(prompt_msgs),
                      getattr(getattr(provider, "config", None), "model", "?"))
            sub = Agent(
                provider=provider, registry=self._agent._registry,
                workspace=getattr(self._agent, "_workspace", None),
                env=getattr(self._agent, "_env", None),
                skill_injector=(lambda sk=skill: [sk.body]),
                skill_whitelist=(lambda sk=skill: set(sk.tools) or None),
                refresh_every=0, stall_enabled=False,  # plan_mode 是 set_plan_mode() 方法,非构造参数
            )
            chunks: list[str] = []
            async for d in sub.run(prompt_msgs):
                if d.error:
                    log.write(f"\n[bold red]❌ {skill_name} 失败[/bold red]  {d.error}")
                    _log.error("isolated skill %s failed: %s", skill_name, d.error)
                    return
                if d.text:
                    chunks.append(d.text)
            summary = "".join(chunks).strip() or "(无输出)"
            _log.info("isolated skill %s done: %d chars", skill_name, len(summary))
            log.write(f"\n[bold cyan]── {skill_name} 结果(独立对话) ──[/bold cyan]\n{summary}")
        except Exception as e:
            _log.exception("isolated skill %s exception", skill_name)
            log.write(f"\n[bold red]❌ {skill_name} 执行异常[/bold red]  {e}")

    def _snapshot(self) -> StatusSnapshot:
        cfg = self._agent._provider.config
        axis, release, ae = self._mode_summary()
        axis_label = "DEFAULT" if axis == "build" else "PLAN"
        return StatusSnapshot(
            axis_label=axis_label, release=release, accept_edits=ae, model=cfg.model,
            tokens_in=self._tokens_in, tokens_out=self._tokens_out,
            ctx_usage=(self._context_manager.current_usage_text()
                       if self._context_manager is not None else ""),
            elapsed_s=max(0.0, time.time() - self._stream_start) if self._stream_start else 0.0,
            session_file=self._session_file,
        )

    def _run_compact(self) -> None:
        """触发异步压缩(编排 run_worker,不阻塞)。"""
        log = self._chat_log()
        if self._context_manager is None:
            log.write("\n[bold red]⚠ 上下文管理未启用[/bold red]")
            return
        self.run_worker(self._cmd_compact(log))

    def _run_resume(self) -> None:
        """/continue:续接达上限而暂停的循环,沿用同一 _msgs 续跑(state10 F5)。"""
        self._streaming = True
        self._stream_start = time.time()
        self.set_interval(1, self._tick)
        self.run_worker(self._process_chat("", resume=True), exclusive=False)

    async def _cmd_compact(self, log) -> None:
        """F6.1 手动压缩:强制生成摘要并改写历史。"""
        try:
            result = await self._context_manager.compact_manual()
        except Exception as e:
            log.write(f"\n[bold red]⚠ 压缩失败: {e}[/bold red]")
            return
        if result is None:
            log.write("\n[dim]── 无可压缩(历史过短或已熔断)[/dim]")
        else:
            log.write("\n[dim]── 已完成手动压缩[/dim]")
        self._refresh_status()

    async def _process_chat(self, text: str, *, resume: bool = False) -> None:
        log = self.query_one("#chat", RichLog)
        inp = self.query_one("#prompt", TextArea)
        stream = self.query_one("#stream", Static)
        tools_container = self.query_one("#tools", Vertical)
        stream.update("")
        tools_container.remove_children()  # 瞬时面板按轮清空(已定型的历史写入 chat 滚动区)
        self._tool_rows.clear()

        buf: list[str] = []
        try:
            # 层2自动触发:仅在真实新消息前判断(续接沿用历史,不重复压缩)
            if not resume and self._context_manager is not None:
                compacted = await self._context_manager.pre_request()
                if compacted is not None:
                    log.write("\n[dim]── 上下文已压缩,早期消息被摘要替代[/dim]")
                    self._refresh_status()
            async for d in self._chat.send_message(text, resume=resume):
                if d.error:
                    stream.update("")
                    log.write(f"\n[bold red]❌ Error[/bold red]  {d.error}")
                    return

                if d.cancelled:
                    stream.update("")
                    log.write("\n[dim]── 已取消[/dim]")
                    return

                if d.paused:
                    # F2/F5 + N1:达上限/空转/未知工具是可续接暂停,非 error
                    stream.update("")
                    reason = d.pause_reason
                    if reason == "limit":
                        log.write(
                            f"\n[dim]── 已达迭代上限(第 {d.resume_round} 轮),"
                            f"输入 /continue 续跑(沿用当前上下文)[/dim]")
                    elif reason == "stall":
                        log.write("\n[dim]── 检测到持续空转,已暂停。输入 /continue 或换一个指令[/dim]")
                    elif reason == "unknown_tool":
                        log.write("\n[dim]── 连续调用未知工具,已暂停[/dim]")
                    else:
                        log.write("\n[dim]── 已暂停[/dim]")
                    return

                if d.stall_hint is not None:
                    # F3:空转软提示(仍继续跑,非停)
                    log.write(f"\n[dim]⚠ {d.stall_hint}[/dim]")

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
        axis_label = "DEFAULT" if axis == ToolAxis.BUILD.value else "PLAN"
        sb = self.query_one(StatusBar)
        ctx_usage = (self._context_manager.current_usage_text()
                     if self._context_manager is not None else "")
        sb.update_state(
            axis=axis, axis_label=axis_label, release=release, accept_edits=ae,
            model=cfg.model, tokens=self._tokens_in + self._tokens_out,
            elapsed_s=max(0.0, time.time() - self._stream_start) if self._stream_start else 0.0,
            ctx_usage=ctx_usage,
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
        # state15: load_skill 成功反馈在首行 ✅;别把长 SOP 折叠成 "N lines" 而藏掉确认
        head = output.split("\n", 1)[0]
        if head.startswith("✅"):
            return head
        lines = output.split("\n")
        if len(lines) == 1 and len(output) <= 60:
            return output
        return f"{len(lines)} lines"


class _CommandContextImpl(CommandContext):
    """把 CommandContext 协议映射到具体渲染框架(Textual)的实现 —— state14 F4。

    命令实现只认协议;本类是换渲染框架时唯一需要重写的一处。
    """

    def __init__(self, app: CodePilotApp) -> None:
        self._app = app

    def show(self, message: str) -> None:
        self._app._chat_log().write("\n" + message)

    def submit_to_ai(self, text: str) -> None:
        self._app._start_ai(text)

    def set_axis(self, axis: ToolAxis, *, announce: bool = False) -> None:
        self._app._set_axis(axis, announce)

    def set_release(self, policy: ReleasePolicy) -> None:
        self._app._set_release(policy)

    def set_accept_edits(self, on: bool | None = None) -> None:
        self._app._set_accept_edits(on)

    def compact(self) -> None:
        self._app._run_compact()

    def resume_loop(self) -> None:
        self._app._run_resume()

    def delete_memory(self, keyword: str) -> int:
        return self._app._delete_memory(keyword)

    def memory_report(self) -> str | None:
        return self._app._memory_report()

    def clear_display(self) -> None:
        self._app._clear_display()

    def reload_skills(self) -> None:
        """/reload：会话内热重载 Skill（/reload 命令 / 协议接口）。"""
        self._app._reload_skills()

    def snapshot(self) -> StatusSnapshot:
        return self._app._snapshot()

    def exit_app(self) -> None:
        self._app.exit()

    def run_isolated_skill(self, skill, params: str) -> None:
        """state15: 独立模式 —— 开独立对话跑完回流摘要(App 端编排 worker)。"""
        self._app.run_worker(
            self._app._run_isolated_skill(skill, params), exclusive=False)