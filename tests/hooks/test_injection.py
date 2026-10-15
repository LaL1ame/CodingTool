"""state16 T8：prompt 注入 volatile 尾块 —— 位置钉住 / 去重 / clear 复位 / 兼容。"""

from codepilot.hooks.injector import HookInjector
from codepilot.prompt import EnvContext, build_system_prompt

ENV = EnvContext(cwd="/ws", date="2026-10-15")


def _build(**kw):
    kw.setdefault("round_num", 1)
    return build_system_prompt(ENV, plan_mode=False, refresh_every=3, **kw)


class TestInjection:
    def test_hook_blocks_placed_after_env_before_memory(self):
        sp = _build(hook_blocks=["HOOK"], memory_blocks=["MEM"],
                    skill_blocks=["SKILL"])
        v = sp.volatile
        assert v.index("SKILL") < v.index("HOOK") < v.index("MEM")
        assert v[0] != "HOOK"  # env 块在最前

    def test_hook_blocks_every_round_rebuilt(self):
        inj = HookInjector()
        inj.add("s#0", "钉住")
        for round_num in (1, 2, 3):
            sp = _build(round_num=round_num, hook_blocks=inj.hook_blocks())
            assert "钉住" in sp.volatile

    def test_empty_injector_compatible(self):
        sp = _build(hook_blocks=[])
        sp2 = _build()
        assert sp.volatile == sp2.volatile  # 空注入与现状一致


class TestInjectorDedup:
    def test_same_key_overwrites(self):
        inj = HookInjector()
        inj.add("s#0", "v1")
        inj.add("s#0", "v2")
        assert inj.hook_blocks() == ["v2"]

    def test_distinct_keys_coexist_in_order(self):
        inj = HookInjector()
        inj.add("s#0", "A")
        inj.add("s#1", "B")
        assert inj.hook_blocks() == ["A", "B"]

    def test_clear_resets(self):
        inj = HookInjector()
        inj.add("s#0", "A")
        inj.clear()
        assert inj.hook_blocks() == []
