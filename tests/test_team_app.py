"""App 级 Team 事件回灌：空闲立即入上下文、流式中先入队后回灌。"""

from collections.abc import AsyncIterator

from codepilot.agent import Agent
from codepilot.config import ProviderConfig
from codepilot.protocols import Delta
from codepilot.providers import BaseProvider
from codepilot.teams.model import TeamEvent
from codepilot.tools.registry import ToolRegistry
from codepilot.ui.app import CodePilotApp


class _Provider(BaseProvider):
    def __init__(self, delay=0.0):
        self.config = ProviderConfig(
            name="m", protocol="anthropic", model="m",
            api_key="k", base_url="http://m")
        self._delay = delay

    async def stream(self, messages, tools=None, system=None) -> AsyncIterator[Delta]:
        if self._delay:
            import asyncio
            await asyncio.sleep(self._delay)
        yield Delta(text="答案")
        yield Delta(done=True)

    async def complete(self, system, messages) -> str:
        return "summary"


class _TeamMgr:
    def __init__(self):
        self.cb = None

    def set_on_event(self, cb):
        self.cb = cb


def _app(delay=0.0):
    agent = Agent(_Provider(delay), ToolRegistry(), workspace=None,
                  stall_enabled=False)
    manager = _TeamMgr()
    return CodePilotApp(agent, team_manager=manager), manager


def _event(summary="alice 发来 plan_request，任务 #1", kind="message"):
    return TeamEvent(
        kind=kind, member="alice", task_id=1, summary=summary)


async def test_team_event_idle_appends_to_context():
    app, manager = _app()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.1)
        assert manager.cb is not None
        app._on_team_event(_event())
        msgs = app._chat._msgs
        assert any("团队事件" in str(m.get("content")) for m in msgs)
        assert any("alice" in str(m.get("content")) for m in msgs)


async def test_team_event_during_stream_is_queued_then_flushed():
    app, manager = _app(delay=0.3)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.1)
        inp = app.query_one("#prompt")
        inp.text = "你好"
        await pilot.press("enter")
        await pilot.pause(0.05)
        assert app._streaming
        app._on_team_event(_event("流式期间的事件"))
        assert app._pending_team
        for _ in range(100):
            await pilot.pause(0.02)
            if not app._streaming:
                break
        assert not app._streaming
        assert not app._pending_team
        assert any("流式期间的事件" in str(m.get("content"))
                   for m in app._chat._msgs)


async def test_team_manager_reachable_from_command_context():
    app, manager = _app()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.05)
        assert app.command_ctx.team_manager is manager


async def test_plan_request_event_gives_focused_instruction():
    """Backlog2/方案A：plan_request 事件回灌必须含聚焦指令与旧结论修正。"""
    app, manager = _app()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.1)
        app._on_team_event(_event(kind="plan_request"))
        joined = "\n".join(
            str(m.get("content")) for m in app._chat._msgs)
        assert "新待审批计划" in joined
        assert "task=#1" in joined or "任务 #1" in joined
        assert "team_inbox" in joined and "team_decide" in joined
        assert "修正" in joined
