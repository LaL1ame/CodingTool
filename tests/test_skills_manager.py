"""F4/F5/F7: phase1 块、激活钉住、白名单并集与安全、deactivate_all。"""

from codepilot.skills.manager import SkillManager
from codepilot.skills.model import Skill, SkillError


def _skill(name, *, mode="shared", tools=(), body="do {input}", desc="desc"):
    return Skill(name=name, description=desc, body=body,
                 tools=frozenset(tools), mode=mode)


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