"""T6 单测 —— 假 CommandContext 驱动全部内置命令(F4:命令不绑定渲染框架)。"""
from codepilot.commands.builtins import build_registry
from codepilot.commands.context import StatusSnapshot
from codepilot.modes import ReleasePolicy, ToolAxis


class FakeCtx:
    """记录所有界面调用的假实现 —— 证明命令只依赖 CommandContext 协议。"""

    def __init__(self):
        self.messages = []
        self.submitted = []
        self.axis = []
        self.release = []
        self.ae = []
        self.compact_calls = 0
        self.resume_calls = 0
        self.deleted = []
        self.mem_report = "MEM"
        self.clears = 0
        self.exited = 0

    def show(self, message):
        self.messages.append(message)

    def submit_to_ai(self, text):
        self.submitted.append(text)

    def set_axis(self, axis, *, announce=False):
        self.axis.append((axis, announce))

    def set_release(self, policy):
        self.release.append(policy)

    def set_accept_edits(self, on=None):
        self.ae.append(on)

    def compact(self):
        self.compact_calls += 1

    def resume_loop(self):
        self.resume_calls += 1

    def delete_memory(self, keyword):
        self.deleted.append(keyword)
        return len(self.deleted)

    def memory_report(self):
        return self.mem_report

    def clear_display(self):
        self.clears += 1

    def snapshot(self):
        return StatusSnapshot(axis_label="DEFAULT", release="manual", accept_edits=False,
                              model="m", tokens_in=1, tokens_out=2, ctx_usage="",
                              elapsed_s=3.0, session_file=None)

    def exit_app(self):
        self.exited += 1


def _cmd(registry, name):
    s = registry.resolve(name)
    assert s is not None, f"命令 /{name} 应已注册"
    return s


def test_help_lists_all_grouped_and_not_hidden():
    r = build_registry()
    ctx = FakeCtx()
    _cmd(r, "help").handler(ctx, "")
    assert len(ctx.messages) == 1
    assert "可用命令" in ctx.messages[0]
    for name in ("clear", "plan", "review"):
        assert f"/{name}" in ctx.messages[0]


def test_plan_do_switch_axis_and_forward():
    r = build_registry()
    ctx = FakeCtx()
    out = _cmd(r, "plan").handler(ctx, "继续做权限")
    assert ctx.axis == [(ToolAxis.PLAN, True)]
    assert out == "继续做权限"               # 带消息 → 转发
    assert _cmd(r, "do").handler(ctx, "") is None   # 无消息 → 本地
    assert ctx.axis[-1] == (ToolAxis.BUILD, True)


def test_review_returns_preset():
    from codepilot.commands.builtins import REVIEW_PROMPT
    ctx = FakeCtx()
    out = _cmd(build_registry(), "review").handler(ctx, "")
    assert out == REVIEW_PROMPT


def test_mode_validation_and_policies():
    r = build_registry()
    ctx = FakeCtx()
    _cmd(r, "mode").handler(ctx, "Auto")     # 大小写不敏感
    assert ctx.release == [ReleasePolicy.AUTO]
    _cmd(r, "mode").handler(ctx, "bogus")    # 非法 → 报错不设
    assert "用法" in ctx.messages[0]
    _cmd(r, "manual").handler(ctx, "")
    assert ctx.release[-1] is ReleasePolicy.MANUAL


def test_edits_on_off_and_toggle():
    r = build_registry()
    ctx = FakeCtx()
    _cmd(r, "edits").handler(ctx, "on")
    assert ctx.ae == [True]
    _cmd(r, "edits").handler(ctx, "OFF")
    assert ctx.ae == [True, False]
    _cmd(r, "edits").handler(ctx, "")
    assert ctx.ae == [True, False, None]   # 无参 → 翻转(None)


def test_compact_continue_delegate():
    r = build_registry()
    ctx = FakeCtx()
    _cmd(r, "compact").handler(ctx, "")
    _cmd(r, "continue").handler(ctx, "")
    assert ctx.compact_calls == 1 and ctx.resume_calls == 1
    assert ctx.messages == []


def test_memory_forget_clear_exit():
    r = build_registry()
    ctx = FakeCtx()
    _cmd(r, "memory").handler(ctx, "")
    assert ctx.messages and "MEM" in "".join(ctx.messages)
    _cmd(r, "forget").handler(ctx, "负责包")
    assert ctx.deleted == ["负责包"]
    _cmd(r, "forget").handler(ctx, "")       # 缺参 → 提示不删
    assert ctx.deleted == ["负责包"]
    _cmd(r, "clear").handler(ctx, "")
    assert ctx.clears == 1
    _cmd(r, "exit").handler(ctx, "")
    assert ctx.exited == 1