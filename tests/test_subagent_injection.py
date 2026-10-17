"""state17: 子 Agent 角色名册注入 system prompt（volatile 块）。"""

from codepilot.prompt.builder import EnvContext, build_system_prompt


def _build(**kw):
    return build_system_prompt(
        EnvContext(cwd=".", date="2026-10-17"),
        plan_mode=False, round_num=1, refresh_every=3, **kw)


def test_subagent_blocks_in_volatile():
    sp = _build(subagent_blocks=["## 可委派的子 Agent 角色\n- architect — 架构评审"])
    assert any("architect" in b for b in sp.volatile)
    assert not any("architect" in b for b in sp.stable)


def test_no_subagent_blocks_unchanged():
    sp = _build()
    assert not any("子 Agent 角色" in b for b in sp.volatile)
