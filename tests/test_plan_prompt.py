"""测试 System Prompt 拼装器 — 优先级/稳定性/环境/频率/标签。"""

from codepilot.prompt import EnvContext, build_system_prompt
from codepilot.prompt.sections import FIXED_SECTIONS


def _sp(**kw):
    env = kw.pop("env", EnvContext("/workspace", "2026-09-20"))
    return build_system_prompt(
        env,
        plan_mode=kw.pop("plan_mode", False),
        round_num=kw.pop("round_num", 1),
        refresh_every=kw.pop("refresh_every", 3),
        **kw,
    )


def test_stable_has_all_seven_in_priority_order():
    sp = _sp(plan_mode=False)
    assert len(sp.stable) == len(FIXED_SECTIONS) == 7
    names_in_order = sorted(FIXED_SECTIONS, key=lambda s: s.priority)
    assert sp.stable == [s.content for s in names_in_order]


def test_stable_identical_across_inputs(cache_premise=True):
    a = _sp(plan_mode=True, round_num=1)
    b = _sp(plan_mode=True, round_num=4)
    # 稳定前缀不随轮次/plan 状态变化（缓存可命中前提）
    assert a.stable == b.stable


def test_env_in_volatile_with_cwd_date():
    sp = _sp(env=EnvContext("/proj", "2026-01-02"), plan_mode=False)
    assert any("/proj" in blk and "2026-01-02" in blk for blk in sp.volatile)


def test_plan_round1_full_injected():
    sp = _sp(plan_mode=True, round_num=1, refresh_every=3)
    tagged = [b for b in sp.volatile if "<session_instruction>" in b]
    assert len(tagged) == 1
    assert "规划模式" in tagged[0]
    assert "继续输出/完善计划" not in tagged[0]  # 首轮是全文，不是精简


def test_plan_round_multiple_of_n_concise():
    sp = _sp(plan_mode=True, round_num=3, refresh_every=3)
    tagged = [b for b in sp.volatile if "<session_instruction>" in b]
    assert len(tagged) == 1
    assert "继续输出/完善计划" in tagged[0]  # 精简文本


def test_plan_other_rounds_no_injection():
    sp = _sp(plan_mode=True, round_num=2, refresh_every=3)
    assert not any("<session_instruction>" in b for b in sp.volatile)


def test_plan_off_never_injects():
    for r in (1, 3, 9):
        sp = _sp(plan_mode=False, round_num=r)
        assert not any("<session_instruction>" in b for b in sp.volatile)


def test_tag_wraps_instruction():
    sp = _sp(plan_mode=True, round_num=1, refresh_every=3)
    tagged = [b for b in sp.volatile if "<session_instruction>" in b][0]
    assert tagged.strip().startswith("<session_instruction>")
    assert tagged.strip().endswith("</session_instruction>")


def test_key_rule_reinforced_in_prompt_and_tools():
    """AC5：关键规则（编辑前必先读）在稳定块与工具描述双通道出现。"""
    from pathlib import Path
    from codepilot.tools.edit import EditTool
    from codepilot.tools.write import WriteTool

    joined = "".join(_sp(plan_mode=False).stable)
    assert "必先读" in joined
    assert "必先读" in EditTool(Path(".")).description
    assert "必先读" in WriteTool(Path(".")).description