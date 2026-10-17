"""测试 agent.py 的 tool_filter 注入点（state17 子 Agent 多层防线的基础）。

tool_filter 在 _build_tools 的 skill 白名单 + plan 只读过滤之后叠加；
不传入时行为与现状一致（向后兼容）。
"""

from codepilot.agent import Agent
from codepilot.config import ProviderConfig
from codepilot.providers import BaseProvider
from codepilot.tools.base import Tool
from codepilot.tools.registry import ToolRegistry


class _MockProvider(BaseProvider):
    def __init__(self):
        self.config = ProviderConfig(name="m", protocol="openai", model="m",
                                     api_key="k", base_url="http://m")

    async def stream(self, messages, tools=None, system=None):
        return
        yield  # pragma: no cover

    async def complete(self, system, messages):
        return ""


class _T(Tool):
    def __init__(self, name, side_effect=False, system_exempt=False):
        self._name = name
        self._side = side_effect
        self._exempt = system_exempt

    @property
    def name(self):
        return self._name

    @property
    def description(self):
        return self._name

    @property
    def parameters(self):
        return {"type": "object", "properties": {}}

    @property
    def side_effect(self):
        return self._side

    @property
    def system_exempt(self):
        return self._exempt

    async def execute(self, **kwargs):
        from codepilot.protocols import ToolResult
        return ToolResult(call_id="", name=self._name, output="ok")


def _agent(registry, **kw) -> Agent:
    return Agent(provider=_MockProvider(), registry=registry,
                 workspace=__file__, **kw)


def _reg(*tools) -> ToolRegistry:
    reg = ToolRegistry()
    for t in tools:
        reg.register(t)
    return reg


def _names(tools_dict):
    """从协议 dict 列表提取工具名（openai 形态）。"""
    if tools_dict is None:
        return None
    return {t["function"]["name"] for t in tools_dict}


def test_no_tool_filter_unchanged():
    """不注入 tool_filter：行为与现状一致（全部工具可见）。"""
    reg = _reg(_T("read"), _T("run", side_effect=True))
    a = _agent(reg)
    assert _names(a._build_tools()) == {"read", "run"}


def test_tool_filter_filters_out():
    """注入 tool_filter：在既有过滤之后叠加，只保留其返回的工具。"""
    reg = _reg(_T("read"), _T("run", side_effect=True), _T("agent", system_exempt=True))
    a = _agent(reg, tool_filter=lambda tools: [t for t in tools if t.name == "read"])
    assert _names(a._build_tools()) == {"read"}


def test_tool_filter_runs_after_plan_mode():
    """tool_filter 在 plan 只读过滤之后调用——只读工具保留、副作用已被滤掉。"""
    reg = _reg(_T("read"), _T("run", side_effect=True))
    a = _agent(reg, tool_filter=lambda tools: list(tools))
    a.set_plan_mode(True)
    assert _names(a._build_tools()) == {"read"}


def test_tool_filter_receives_tool_objects():
    """tool_filter 收到的输入是 Tool 对象（协议转换前），含系统工具。"""
    reg = _reg(_T("read"), _T("agent", system_exempt=True))
    seen = []
    def f(tools):
        seen.append([t.name for t in tools])
        return list(tools)
    a = _agent(reg, tool_filter=f)
    a._build_tools()
    assert "agent" in seen[0]
    assert "read" in seen[0]
