"""F4/F5/F7: phase1 块、激活钉住、白名单并集与安全、deactivate_all。"""

from codepilot.skills.manager import SkillManager
from codepilot.skills.model import Skill, SkillError


def _skill(name, *, mode="shared", tools=(), body="do {input}", desc="desc",
           auto_prompt=""):
    return Skill(name=name, description=desc, body=body,
                 tools=frozenset(tools), mode=mode, auto_prompt=auto_prompt)


def test_phase1_block_lists_names_and_desc():
    m = SkillManager({"a": _skill("a", desc="alpha"), "b": _skill("b", desc="beta")})
    block = m.phase1_block()
    assert "a" in block and "alpha" in block
    assert "b" in block and "beta" in block


def test_load_renders_activates_and_registers_command():
    calls = []
    m = SkillManager({"commit": _skill("commit", body="按 {input} 提交", tools={"read"})},
                     on_register_command=lambda n, h: calls.append(n))
    rendered = m.load("commit", "  优化性能  ")
    assert rendered == "按 优化性能 提交"
    assert "commit" in m.active
    assert rendered in m.active_blocks()
    assert calls == ["commit"]


def test_active_whitelist_union_of_shared_skills():
    m = SkillManager({"a": _skill("a", tools={"read", "grep"}),
                      "b": _skill("b", tools={"run"})})
    m.load("a")
    m.load("b")
    assert m.active_whitelist() == {"read", "grep", "run"}


def test_active_whitelist_none_when_shared_skill_unconstrained():
    # 有 shared skill tools 为空（不约束）→ 整体不收窄，防把工具全藏了
    m = SkillManager({"a": _skill("a", tools={"read"}), "b": _skill("b", tools=())})
    m.load("a")
    m.load("b")
    assert m.active_whitelist() is None


def test_isolated_skill_excluded_from_main_whitelist():
    m = SkillManager({"i": _skill("i", mode="isolated", tools={"run"})})
    m.load("i")
    assert m.active_whitelist() is None  # 孤立型不进主对话白名单


def test_deactivate_all_resets_active_but_keeps_known():
    m = SkillManager({"a": _skill("a", tools={"read"})})
    m.load("a")
    assert m.active
    m.deactivate_all()
    assert not m.active
    assert m.active_whitelist() is None
    assert "a" in m.known  # known 保留


def test_require_unknown_raises():
    m = SkillManager({})
    try:
        m.require("nope")
        assert False, "should raise"
    except SkillError:
        pass


class _FakeCtx:
    def __init__(self):
        self.shown = []
        self.calls = []

    def show(self, msg):
        self.shown.append(msg)

    def run_isolated_skill(self, skill, params):
        self.calls.append((skill.name, params))


def test_command_handler_shared_shows_success():
    m = SkillManager({"commit": _skill("commit", tools=("read", "run"))})
    ctx = _FakeCtx()
    out = m.command_handler("commit")(ctx, " 发个提交  ")
    # 共享：先反馈成功，再把参数转发 AI
    assert out == "发个提交"
    assert any("已加载并激活 Skill「commit」" in s and "read" in s for s in ctx.shown)
    assert "commit" in m.active


def test_command_bare_with_auto_prompt_injects_requirement():
    """裸敲 skill 命令且设了 auto_prompt → 把标准要求自动发 AI(旧 /review 即时感)。"""
    m = SkillManager({"code-review": _skill("code-review",
                                             auto_prompt="请按规范审查改动")})
    ctx = _FakeCtx()
    out = m.command_handler("code-review")(ctx, "")
    assert out == "请按规范审查改动"          # 裸命令自动注入要求,不等待
    assert "code-review" in m.active           # SOP 同时钉住
    assert any("已加载并激活" in s for s in ctx.shown)


def test_command_bare_without_auto_prompt_waits():
    """无 auto_prompt 的共享 skill:裸命令只激活钉住、等发令(不转发)。"""
    m = SkillManager({"mkdocs": _skill("mkdocs")})
    ctx = _FakeCtx()
    out = m.command_handler("mkdocs")(ctx, "")
    assert out is None
    assert "mkdocs" in m.active


def test_command_handler_isolated_shows_start_and_runs():
    m = SkillManager({"review": _skill("review", mode="isolated", tools=("read",))})
    ctx = _FakeCtx()
    out = m.command_handler("review")(ctx, "")
    assert out is None                        # isolated：不转发，走独立执行
    assert any("开始独立执行 Skill「review」" in s for s in ctx.shown)
    assert ctx.calls == [("review", "")]
    assert "review" not in m.active           # 隔离型不钉入主对话


def test_success_message_lists_mode_and_whitelist():
    m = SkillManager({"x": _skill("x", tools=("run", "read"))})
    msg = m.success_message("x")
    assert "「x」" in msg
    assert "shared" in msg and "run" in msg and "read" in msg


def test_reload_requres_refetch_or_raises():
    m = SkillManager({})
    try:
        m.reload()
        assert False, "should raise"
    except SkillError:
        pass


def test_reload_replaces_known_and_reports_diff():
    # 模拟:启动只有 a;用户在会话中新建了 b、删了 a → /reload 重扫
    initial = {"a": _skill("a")}
    m = SkillManager(initial, refetch=lambda: {"b": _skill("b")})
    m.load("a")                       # 先激活 a
    added, removed = m.reload()
    assert added == ["b"]
    assert removed == ["a"]
    assert "b" in m.known and "a" not in m.known
    assert not m.active               # 重载复位激活态(内容可能变)
    assert m.active_whitelist() is None