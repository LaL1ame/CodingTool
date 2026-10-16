"""state16 T9：agent 发射点 —— tool_pre 拦截 / tool_post / round_start / stop。

验证:
  · tool_pre 非零退出 → 工具未执行、Hook 拦截原因作工具结果回灌
  · 放行 / fail-open（hook 命令异常、超时） → 工具正常执行
  · 权限拒绝的工具不触发 tool_pre（权限先于 Hook）
  · round_start 每轮发射、stop 四出口发射（done/limit 为代表）
"""

import sys

from codepilot.agent import Agent
from codepilot.hooks.engine import HookEngine
from codepilot.hooks.injector import HookInjector
from codepilot.hooks.model import CondItem, Condition, HookAction, HookRule
from codepilot.permissions import PermissionManager
from codepilot.permissions.mode import PermissionMode
from codepilot.permissions.rules import Rule, Verdict
from codepilot.protocols import Delta, ToolCall
from codepilot.tools.registry import ToolRegistry

from tests.test_agent import _MockProvider, _SlowTool

PY = sys.executable


def _cmd_rule(cmd, event="tool_pre", cond=None, ordinal=0):
    return HookRule(
        event=event, condition=cond,
        action=HookAction(type="command", fields={"cmd": cmd}),
        source="t", ordinal=ordinal)


def _agent(rounds, engine, registry=None, permission=None, **kw):
    reg = registry or ToolRegistry()
    provider = _MockProvider(rounds=rounds)
    return Agent(provider, reg, workspace=None, hooks=engine,
                 permission=permission, stall_enabled=False, **kw)


async def _collect(agent, msgs):
    return [d async for d in agent.run(msgs)]


class _SpyTool(_SlowTool):
    def __init__(self, name):
        super().__init__(name)
        self.calls = 0

    async def execute(self, **kwargs):
        self.calls += 1
        return await super().execute(**kwargs)


def _one_call_rounds(tool_name="read"):
    return [
        [Delta(tool_calls=[ToolCall(id="1", name=tool_name, arguments={"command": "ls"})])],
        [Delta(text="done"), Delta(done=True)],
    ]


class TestToolPreIntercept:
    async def test_denied_tool_not_executed_and_reason_fed_back(self):
        tool = _SpyTool("run")
        reg = ToolRegistry()
        reg.register(tool)
        engine = HookEngine(
            [_cmd_rule(f'"{PY}" -c "import sys; sys.stderr.write(\\"危险命令\\"); sys.exit(1)"')],
            HookInjector())
        agent = _agent(_one_call_rounds("run"), engine, registry=reg)
        deltas = await _collect(agent, [])
        assert tool.calls == 0  # 工具未执行
        results = [d.tool_result for d in deltas if d.tool_result]
        assert len(results) == 1
        assert results[0].error == "Hook 拦截: 危险命令"
        # 循环未被打断：后续轮正常 done
        assert any(d.done for d in deltas)

    async def test_allowed_when_exit_zero(self):
        tool = _SpyTool("run")
        reg = ToolRegistry()
        reg.register(tool)
        engine = HookEngine([_cmd_rule(f'"{PY}" -c "pass"')], HookInjector())
        agent = _agent(_one_call_rounds("run"), engine, registry=reg)
        deltas = await _collect(agent, [])
        assert tool.calls == 1
        assert any(d.done for d in deltas)

    async def test_fail_open_on_hook_error(self, monkeypatch):
        # shell 启动失败的形态：run_command 返回 error（真实 spawn 失败难以稳定构造，打桩模拟）
        from codepilot.hooks import engine as engine_mod
        from codepilot.hooks.actions import CommandOutcome

        async def _boom(*a, **kw):
            return CommandOutcome(exit_code=-1, stdout="", stderr="",
                                  error="spawn failed")
        monkeypatch.setattr(engine_mod, "run_command", _boom)

        tool = _SpyTool("run")
        reg = ToolRegistry()
        reg.register(tool)
        engine = HookEngine([_cmd_rule("whatever")], HookInjector())
        agent = _agent(_one_call_rounds("run"), engine, registry=reg)
        deltas = await _collect(agent, [])
        assert tool.calls == 1  # 异常 → 放行
        assert any(d.done for d in deltas)

    async def test_fail_open_on_hook_timeout(self):
        tool = _SpyTool("run")
        reg = ToolRegistry()
        reg.register(tool)
        rule = HookRule(
            event="tool_pre", condition=None,
            action=HookAction(type="command",
                              fields={"cmd": f'"{PY}" -c "import time; time.sleep(5)"'},
                              timeout=0.3),
            source="t", ordinal=0)
        engine = HookEngine([rule], HookInjector())
        agent = _agent(_one_call_rounds("run"), engine, registry=reg)
        deltas = await _collect(agent, [])
        assert tool.calls == 1  # 超时 → 放行
        assert any(d.done for d in deltas)

    async def test_condition_scopes_interception(self):
        # 条件只拦 run 且 command 含 rm；本调用是 ls → 不拦
        tool = _SpyTool("run")
        reg = ToolRegistry()
        reg.register(tool)
        c = Condition(mode="all", items=(
            CondItem("tool", "run"), CondItem("command", "*rm -rf*")))
        engine = HookEngine(
            [_cmd_rule(f'"{PY}" -c "import sys; sys.exit(1)"', cond=c)],
            HookInjector())
        agent = _agent(_one_call_rounds("run"), engine, registry=reg)
        await _collect(agent, [])
        assert tool.calls == 1

    async def test_permission_deny_skips_hook(self):
        tool = _SpyTool("run")
        reg = ToolRegistry()
        reg.register(tool)

        class _SpyEngine(HookEngine):
            def __init__(self):
                super().__init__([], HookInjector())
                self.pre_calls = 0

            async def fire_pre_tool(self, tool, arguments):
                self.pre_calls += 1
                return None

        engine = _SpyEngine()
        permission = PermissionManager(
            workspace=None, mode=PermissionMode.DEFAULT,
            rules=[Rule(tool="run", pattern="*", verdict=Verdict.DENY)])
        agent = _agent(_one_call_rounds("run"), engine, registry=reg,
                       permission=permission)
        deltas = await _collect(agent, [])
        assert tool.calls == 0
        assert engine.pre_calls == 0  # 权限拒绝 → 不触发 tool_pre
        results = [d.tool_result for d in deltas if d.tool_result]
        assert "权限拒绝" in results[0].error


class TestToolPost:
    async def test_tool_post_fires_with_ok(self):
        reg = ToolRegistry()
        reg.register(_SlowTool("read"))
        seen = []

        class _SpyEngine(HookEngine):
            def __init__(self):
                super().__init__([], HookInjector())

            async def fire(self, event, fields):
                seen.append((event, dict(fields)))

        agent = _agent(_one_call_rounds("read"), _SpyEngine(), registry=reg)
        await _collect(agent, [])
        posts = [f for e, f in seen if e == "tool_post"]
        assert len(posts) == 1
        assert posts[0]["tool"] == "read"
        assert posts[0]["ok"] is True  # spy 绕过引擎字符串化，看到的是原始 bool


class TestUserMessage:
    async def test_user_message_fires_not_on_resume(self):
        from codepilot.chat import ChatManager

        reg = ToolRegistry()
        reg.register(_SlowTool("read"))
        seen = []

        class _SpyEngine(HookEngine):
            def __init__(self):
                super().__init__([], HookInjector())

            async def fire(self, event, fields):
                seen.append((event, dict(fields)))

        agent = _agent(_one_call_rounds("read"), _SpyEngine(), registry=reg)
        chat = ChatManager(agent, hooks=agent._hooks)
        deltas = [d async for d in chat.send_message("hello")]
        assert any(d.done for d in deltas)
        msgs = [f for e, f in seen if e == "user_message"]
        assert msgs == [{"text": "hello"}]
        # resume 续接不重发 user_message
        await _collect(agent, [])
        assert len([e for e, _ in seen if e == "user_message"]) == 1


class TestRoundAndStop:
    async def test_round_start_fires_each_round_and_stop_done(self):
        reg = ToolRegistry()
        reg.register(_SlowTool("read"))
        seen = []

        class _SpyEngine(HookEngine):
            def __init__(self):
                super().__init__([], HookInjector())

            async def fire(self, event, fields):
                seen.append((event, dict(fields)))

        agent = _agent(_one_call_rounds("read"), _SpyEngine(), registry=reg)
        await _collect(agent, [])
        rounds = [f["round"] for e, f in seen if e == "round_start"]
        assert rounds == [1, 2]  # 两轮各一次（spy 看到原始 int；引擎层才 str 化）
        stops = [f for e, f in seen if e == "stop"]
        assert stops == [{"reason": "done"}]

    async def test_stop_fires_on_limit(self):
        reg = ToolRegistry()
        reg.register(_SlowTool("read"))
        seen = []

        class _SpyEngine(HookEngine):
            def __init__(self):
                super().__init__([], HookInjector())

            async def fire(self, event, fields):
                seen.append((event, dict(fields)))

        rounds = [[Delta(tool_calls=[ToolCall(id=str(i), name="read", arguments={})])]
                  for i in range(3)]
        agent = _agent(rounds, _SpyEngine(), registry=reg, max_rounds=2)
        deltas = await _collect(agent, [])
        assert any(d.paused and d.pause_reason == "limit" for d in deltas)
        stops = [f for e, f in seen if e == "stop"]
        assert stops == [{"reason": "limit"}]

    async def test_no_hooks_behavior_unchanged(self):
        reg = ToolRegistry()
        reg.register(_SlowTool("read"))
        provider = _MockProvider(rounds=_one_call_rounds("read"))
        agent = Agent(provider, reg, workspace=None, stall_enabled=False)
        deltas = await _collect(agent, [])
        assert any(d.done for d in deltas)
        assert len([d for d in deltas if d.tool_result]) == 1
