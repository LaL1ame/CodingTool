"""F5/F7: Agent 集成——技能钉住块注入 + 白名单收窄∩plan + 系统工具恒可见。"""

import asyncio

from codepilot.agent import Agent
from codepilot.protocols import Delta
from codepilot.prompt import EnvContext
from codepilot.tools.base import Tool
from codepilot.tools.registry import ToolRegistry


class _T(Tool):
    def __init__(self, name, *, side_effect=False, system=False):
        self._name = name
        self._se = side_effect
        self._sys = system

    @property
    def name(self): return self._name

    @property
    def description(self): return "d"

    @property
    def parameters(self): return {"type": "object", "properties": {}, "required": []}

    @property
    def side_effect(self): return self._se

    @property
    def system_exempt(self): return self._sys

    async def execute(self, **k): return None


class _FakeConfig:
    protocol = "anthropic"


class _CapturingProvider:
    def __init__(self):
        self.config = _FakeConfig()
        self.seen_systems = []

    async def stream(self, messages, tools=None, system=None):
        self.seen_systems.append(system)
        yield Delta(text="hi")  # 无工具调用 → 单轮 done


def _registry():
    reg = ToolRegistry()
    reg.register(_T("read"))
    reg.register(_T("write", side_effect=True))
    reg.register(_T("run", side_effect=True))
    reg.register(_T("load_skill", system=True))
    return reg


def _agent(reg=None, whitelist=None, injector=None):
    return Agent(
        provider=_CapturingProvider(), registry=reg or _registry(),
        workspace=__import__("pathlib").Path("."),
        skill_injector=injector, skill_whitelist=whitelist,
    )


def _names(dt: list | None) -> set:
    if not dt:
        return set()
    return {d["name"] for d in dt}


def test_no_whitelist_all_tools():
    a = _agent()
    dt = a._build_tools()
    assert _names(dt) == {"read", "write", "run", "load_skill"}


def test_whitelist_narrows_but_keeps_system():
    a = _agent(whitelist=lambda: {"read", "run"})
    dt = a._build_tools()
    # 白名单收窄到 read/run，但系统 load_skill 恒在
    assert _names(dt) == {"read", "run", "load_skill"}


def test_whitelist_with_plan_intersection():
    a = _agent(whitelist=lambda: {"read", "run"})  # run 有副作用
    a.set_plan_mode(True)
    dt = a._build_tools()
    # plan ∩ 白名单：read 留下，run(副作用) 被滤，load_skill 系统恒在
    assert _names(dt) == {"read", "load_skill"}


def test_whitelist_none_disables_narrowing():
    a = _agent(whitelist=lambda: None)  # manager 返回 None = 不收窄
    dt = a._build_tools()
    assert _names(dt) == {"read", "write", "run", "load_skill"}


def test_run_injects_skill_blocks():
    captured = _CapturingProvider()
    reg = _registry()
    a = Agent(provider=captured, registry=reg, workspace=__import__("pathlib").Path("."),
              env=EnvContext(cwd=".", date="2026-01-01"),
              skill_injector=lambda: ["SKILL-PINNED-BLOCK"])

    async def _go():
        out = []
        async for d in a.run([]):
            out.append(d)
        return out

    deltas = asyncio.run(_go())
    assert any(d.done for d in deltas)
    # provider 收到 system，其中 skill 块被注入
    sys_snap = captured.seen_systems[-1]
    flat = sys_snap.stable + sys_snap.volatile
    assert any("SKILL-PINNED-BLOCK" in b for b in flat)