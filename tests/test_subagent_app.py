"""App 级接线测试（F11）：提示行、F3 打开面板、流式结束焦点恢复。"""

from collections.abc import AsyncIterator

from textual.widgets import Static

from codepilot.agent import Agent
from codepilot.config import ProviderConfig
from codepilot.protocols import Delta, ToolResult
from codepilot.providers import BaseProvider
from codepilot.subagents.model import SubAgentTask
from codepilot.tools.registry import ToolRegistry
from codepilot.ui.app import CodePilotApp
from codepilot.ui.screens.subagent_screen import SubAgentListScreen


def test_agent_result_surfaces_worktree_line():
    """前台 agent 多行结果折叠时，Worktree 保留/清理行必须仍然可见。"""
    result = ToolResult(
        call_id="", name="agent",
        output="结论第一行\n第二行\n"
               "[Worktree] 保留 /tmp/wt（分支 codepilot/x，kept/dirty）")
    shown = CodePilotApp._format_result(result)
    assert "lines" in shown
    assert "Worktree" in shown
    assert "保留" in shown


def test_non_agent_result_still_collapses():
    result = ToolResult(call_id="", name="grep", output="a\nb\nc")
    assert CodePilotApp._format_result(result) == "3 lines"


class _Provider(BaseProvider):
    def __init__(self, delay=0.0):
        self.config = ProviderConfig(name="m", protocol="anthropic", model="m",
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


class _FakeManager:
    def __init__(self, tasks):
        self._tasks = tasks

    def tasks(self):
        return list(self._tasks)

    def counts(self):
        running = sum(
            1 for t in self._tasks if t.status in ("running", "backgrounded"))
        done = sum(1 for t in self._tasks if t.status == "done")
        return running, done

    def set_on_task_done(self, cb):
        self._cb = cb

    def set_parent_msgs(self, getter):
        pass

    def set_parent_visible(self, getter):
        pass


def _app(tasks, delay=0.0):
    agent = Agent(_Provider(delay), ToolRegistry(), workspace=None,
                  stall_enabled=False)
    return CodePilotApp(agent, subagent_manager=_FakeManager(tasks))


def _task(status="running"):
    return SubAgentTask(task_id=1, kind="fork", role=None, status=status,
                        prompt="总结当前上下文", created_at=0.0)


async def test_hint_line_shows_running_count():
    app = _app([_task()])
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.2)
        hint = app.query_one("#subagent-task-hint", Static)
        assert hint.display
        assert "1 运行中" in str(hint.content)
        assert "F3" in str(hint.content)


async def test_f3_opens_panel_and_esc_restores_prompt_focus():
    app = _app([_task("done")])
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.1)
        await pilot.press("f3")
        await pilot.pause()
        assert isinstance(app.screen, SubAgentListScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is app.screen_stack[0]
        assert app.focused is not None and app.focused.id == "prompt"


async def test_focus_returns_to_prompt_after_streaming():
    """Textual 在 disabled 时 blur；每轮结束必须把焦点还给输入框。"""
    app = _app([_task("done")])
    async with app.run_test(size=(120, 40)) as pilot:
        inp = app.query_one("#prompt")
        inp.text = "你好"
        await pilot.press("enter")
        for _ in range(100):
            await pilot.pause(0.02)
            if not app._streaming:
                break
        assert not app._streaming
        assert app.focused is not None and app.focused.id == "prompt"


async def test_modal_open_during_stream_keeps_prompt_focus_after_close():
    """流式中按 F3：结束后焦点应回到底层输入框，关闭面板可直接输入。"""
    app = _app([_task("done")], delay=0.4)
    async with app.run_test(size=(120, 40)) as pilot:
        inp = app.query_one("#prompt")
        inp.text = "你好"
        await pilot.press("enter")
        for _ in range(30):
            await pilot.pause(0.01)
            if app._streaming:
                break
        assert app._streaming
        await pilot.press("f3")
        await pilot.pause()
        assert isinstance(app.screen, SubAgentListScreen)
        for _ in range(100):
            await pilot.pause(0.02)
            if not app._streaming:
                break
        assert not app._streaming
        await pilot.press("escape")
        await pilot.pause()
        assert app.focused is not None and app.focused.id == "prompt"
