"""内置命令全集(state14, F7) —— 装配 16 条命令 + REVIEW_PROMPT。

处理函数统一签名 handler(ctx: CommandContext, params: str) -> str | None:
- 返回一条用户消息 → 分发器转发给 AI
- 返回 None → 本地处理完(通过 ctx.show 呈现)
本模块不 import App/Textual;命令只用 CommandContext 协议触达界面。
"""
from __future__ import annotations

from codepilot.commands.registry import (
    CommandKind,
    CommandRegistry,
    CommandSpec,
)
from codepilot.modes import ReleasePolicy, ToolAxis

# /review 的预设提示词(PROMPT 类型):把该文案作一条用户消息送进对话交给 AI。
REVIEW_PROMPT = (
    "请审查当前工作区最近的 git 改动,指出潜在问题:正确性、安全性、可维护性、"
    "是否有未覆盖的边界情况。若无明显改动,请说明你观察到了什么。"
)

_KIND_LABELS = {
    CommandKind.LOCAL: "本地",
    CommandKind.UI: "界面/状态",
    CommandKind.PROMPT: "提示词→AI",
}

_POLICIES = {"manual": ReleasePolicy.MANUAL, "auto": ReleasePolicy.AUTO,
             "strict": ReleasePolicy.STRICT}


def _require_kw(ctx, params: str) -> str | None:
    """返回 keyword;缺参时给出用法提示并返回 None(不执行)。"""
    kw = params.strip()
    if kw:
        return kw
    ctx.show("用法: 该命令需一个关键词参数")
    return None


def build_registry() -> CommandRegistry:
    """登记全部内置命令,返回注册中心。16 条不撞,可直接 assert_no_conflicts。"""
    r = CommandRegistry()

    def _register(name, kind, desc, handler, *, aliases=(), usage="", arg_hint=None,
                  hidden=False) -> None:
        r.register(CommandSpec(
            name=name, kind=kind, desc=desc, handler=handler,
            aliases=aliases, usage=usage, arg_hint=arg_hint, hidden=hidden,
        ))

    # —— LOCAL: 纯本地/查询 ——
    def h_help(ctx, params):
        lines = ["[bold]可用命令[/bold] (输入 /help 或 /? 查看本清单)"]
        for kind, label in _KIND_LABELS.items():
            cmds = [s for s in r.all(include_hidden=False) if s.kind is kind]
            if not cmds:
                continue
            lines.append(f"[bold]{label}[/bold]")
            for s in cmds:
                usage = f" {s.usage}" if s.usage else ""
                desc = f" — {s.desc}" if s.desc else ""
                lines.append(f"  /{s.name}{usage}{desc}")
        ctx.show("\n".join(lines))
    _register("help", CommandKind.LOCAL, "列出可用命令与用法", h_help,
              aliases=("?",), usage="")

    def h_exit(ctx, params):
        ctx.exit_app()
    _register("exit", CommandKind.LOCAL, "退出程序", h_exit, aliases=("quit",))

    def h_memory(ctx, params):
        report = ctx.memory_report()
        if report:
            ctx.show(report)
        else:
            ctx.show("记忆系统未启用")
    _register("memory", CommandKind.LOCAL, "分组展示当前指令与记忆索引", h_memory)

    def h_session(ctx, params):
        snap = ctx.snapshot()
        if not snap.session_file:
            ctx.show("当前无会话文件")
            return
        from codepilot.session import store
        info = store.summarize(snap.session_file)
        lines = [f"[bold]会话[/bold] {snap.session_file}"]
        if info:
            lines.append(f"消息数: {info.get('message_count', '?')}")
            lines.append(f"ID: {info.get('id', '?')}")
        ctx.show("\n".join(lines))
    _register("session", CommandKind.LOCAL, "显示当前会话信息", h_session)

    def h_status(ctx, params):
        s = ctx.snapshot()
        ctx.show(
            f"[bold]状态[/bold] 模式 [{s.axis_label}] · 放行 {s.release} · "
            f"自动编辑 {'ON' if s.accept_edits else 'OFF'}\n"
            f"模型 {s.model}  ↑{s.tokens_in}t ↓{s.tokens_out}t  ⏱{s.elapsed_s:.0f}s\n"
            f"{s.ctx_usage or ''}".rstrip()
        )
    _register("status", CommandKind.LOCAL, "显示 token/上下文/模式等状态", h_status)

    # —— UI: 影响界面/应用状态机 ——
    def h_plan(ctx, params):
        ctx.set_axis(ToolAxis.PLAN, announce=True)
        return params or None
    _register("plan", CommandKind.UI, "进入计划模式(只读工具),可带消息", h_plan,
              usage="[消息]", arg_hint="消息? ")

    def h_do(ctx, params):
        ctx.set_axis(ToolAxis.BUILD, announce=True)
        return params or None
    _register("do", CommandKind.UI, "退回执行模式,可带消息", h_do,
              usage="[消息]", arg_hint="消息? ")

    def h_mode(ctx, params):
        name = params.strip().lower()
        if name not in _POLICIES:
            ctx.show("用法: /mode manual|auto|strict")
            return
        ctx.set_release(_POLICIES[name])
    _register("mode", CommandKind.UI, "设置放行策略 manual/auto/strict", h_mode,
              usage="<manual|auto|strict>", arg_hint="manual|auto|strict")

    for _name, _pol in (("auto", ReleasePolicy.AUTO),
                        ("manual", ReleasePolicy.MANUAL),
                        ("strict", ReleasePolicy.STRICT)):
        def h_single(ctx, params, _pol=_pol):
            ctx.set_release(_pol)
        _register(_name, CommandKind.UI, f"放行策略→{_pol.value}", h_single)

    def h_edits(ctx, params):
        on = {"on": True, "off": False}.get(params.strip().lower())
        ctx.set_accept_edits(on)   # on=指定 / None=翻转
    _register("edits", CommandKind.UI, "自动编辑开关(无参即翻转)", h_edits,
              usage="[on|off]", arg_hint="on|off")

    def h_compact(ctx, params):
        ctx.compact()
    _register("compact", CommandKind.UI, "手动压缩上下文", h_compact)

    def h_continue(ctx, params):
        ctx.resume_loop()
    _register("continue", CommandKind.UI, "续接暂停的 Agent 循环", h_continue)

    def h_forget(ctx, params):
        kw = _require_kw(ctx, params)
        if kw is None:
            return
        n = ctx.delete_memory(kw)
        ctx.show(f"已删除 {n} 条记忆条目")
    _register("forget", CommandKind.UI, "按关键词删除记忆条目(幂等)", h_forget,
              usage="<关键词>", arg_hint="关键词")

    def h_clear(ctx, params):
        ctx.clear_display()
    _register("clear", CommandKind.UI, "清屏显示,保留历史与上下文", h_clear,
              aliases=("cls",))

    def h_reload(ctx, params):
        ctx.reload_skills()
    _register("reload", CommandKind.UI, "会话内热重载 Skill(重扫三级+更新命令)", h_reload)

    def h_permission(ctx, params):
        s = ctx.snapshot()
        ctx.show(
            f"[bold]权限[/bold] 模式 [{s.axis_label}] · 放行 {s.release} · "
            f"自动编辑 {'ON' if s.accept_edits else 'OFF'}"
        )
    _register("permission", CommandKind.UI, "显示当前权限模式与自动编辑状态", h_permission)

    # —— state17: SubAgent 后台任务管理（经 getattr 访问 manager，未启用时安全降级）——
    def _subagent_manager(ctx):
        return getattr(ctx, "subagent_manager", None)

    def h_agents(ctx, params):
        mgr = _subagent_manager(ctx)
        if mgr is None:
            ctx.show("SubAgent 系统未启用")
            return
        tasks = mgr.tasks()
        if not tasks:
            ctx.show("当前没有子 Agent 任务")
            return
        lines = ["[bold]子 Agent 任务[/bold] (id | 类型 | 角色 | 状态 | 用量)"]
        for t in tasks:
            usage = f"{t.usage.input_tokens}t" if t.usage else "-"
            lines.append(f"  #{t.task_id} | {t.kind} | {t.role or '-'} | {t.status} | {usage}")
        ctx.show("\n".join(lines))
    _register("agents", CommandKind.LOCAL, "列出子 Agent 后台任务", h_agents)

    def h_bg(ctx, params):
        mgr = _subagent_manager(ctx)
        if mgr is None:
            ctx.show("SubAgent 系统未启用")
            return
        kw = params.strip()
        if not kw:
            ctx.show("用法: /bg <task_id> —— 把前台运行中的子 Agent 任务切后台")
            return
        try:
            task_id = int(kw)
        except ValueError:
            ctx.show(f"无效 task_id: {kw!r}")
            return
        if mgr.background_now(task_id):
            ctx.show(f"已把子 Agent 任务 #{task_id} 切到后台")
        else:
            ctx.show(f"任务 #{task_id} 不在前台等待中（可能已完成或非前台）")
    _register("bg", CommandKind.LOCAL, "把前台子 Agent 任务切后台", h_bg,
              usage="<task_id>", arg_hint="task_id")

    # state15: /review 命令已移交内置 review Skill（统一入口）。旧 PROMPT 载荷
    # 并入 codepilot/skills/data/review/SKILL.md 正文；REVIEW_PROMPT 保留仅向后兼容。
    # —— PROMPT: 预设提示词送 AI ——
    return r