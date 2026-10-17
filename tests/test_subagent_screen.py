"""子 Agent 任务面板测试（F11，headless Textual）。"""

from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import Label, ListView, RichLog, Static

from codepilot.subagents.model import SubAgentTask
from codepilot.subagents.transcript import TranscriptEntry
from codepilot.ui.screens.subagent_screen import (
    SubAgentListScreen,
    SubAgentTranscriptScreen,
    format_task_row,
)

_THEME = (Path(__file__).resolve().parent.parent /
          "codepilot" / "ui" / "theme.tcss").read_text(encoding="utf-8")


class Host(App):
    CSS = _THEME

    def compose(self) -> ComposeResult:
        yield Static("placeholder")


def _task(task_id=1, *, status="done", role="architect", kind="defined",
          summary="结论", entries=None, error=""):
    return SubAgentTask(
        task_id=task_id, kind=kind, role=role, status=status,
        prompt="审查这段设计", summary=summary,
        error=error, created_at=0.0,
        entries=tuple(entries or ()),
    )


def _done_entries():
    return [
        TranscriptEntry("user", "请审查"),
        TranscriptEntry("assistant", "我来读文件"),
        TranscriptEntry("tool_use", '{"path": "a.py"}', "read"),
        TranscriptEntry("tool_result", "文件内容", "read"),
        TranscriptEntry("assistant", "结论：通过"),
    ]


def _line_texts(log: RichLog) -> list[str]:
    return [getattr(strip, "text", "") for strip in log.lines]


def test_format_task_row_running_and_done():
    running = format_task_row(_task(1, status="running", role="r"), now=3.0)
    assert "#1" in running and "运行中" in running and "[角色] r" in running
    done = format_task_row(_task(2, status="done", kind="fork", role=None), now=3.0)
    assert "[Fork]" in done and "完成" in done


def test_elapsed_fixed_after_finish():
    """已完成的耗时固定为 finished_at-created_at，不随查看时刻增长（真机 711.1s 修正）。"""
    task = _task(1, status="done")
    task.created_at = 10.0
    task.finished_at = 13.0
    row = format_task_row(task, now=1000.0)
    assert "3.0s" in row
    assert "993.0s" not in row


async def test_list_renders_rows_and_focus():
    tasks = [_task(1), _task(2, role="reviewer")]
    app = Host()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = SubAgentListScreen(lambda: tasks)
        await app.push_screen(screen)
        await pilot.pause()
        lv = screen.query_one(ListView)
        assert len(lv.children) == 2
        assert lv.has_focus
        label = lv.children[0].query_one(Label)
        assert "architect" in str(label.content)


async def test_arrow_and_enter_open_transcript_esc_returns_with_index():
    tasks = [_task(1, entries=_done_entries()), _task(2, role="reviewer",
                                                     entries=_done_entries())]
    app = Host()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = SubAgentListScreen(lambda: tasks)
        await app.push_screen(screen)
        await pilot.pause()
        await pilot.press("down")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, SubAgentTranscriptScreen)
        log = app.screen.query_one(RichLog)
        texts = "\n".join(_line_texts(log))
        assert "结论：通过" in texts and "read" in texts
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is screen
        assert screen.query_one(ListView).index == 1


async def test_esc_on_list_closes():
    app = Host()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = SubAgentListScreen(lambda: [_task(1)])
        await app.push_screen(screen)
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is not screen


async def test_refresh_updates_status_and_keeps_selection():
    t1 = _task(1, status="done")
    t2 = _task(2, status="running", role="reviewer")
    app = Host()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = SubAgentListScreen(lambda: [t1, t2])
        await app.push_screen(screen)
        await pilot.pause()
        lv = screen.query_one(ListView)
        lv.index = 1
        t2.status = "done"
        await pilot.pause(0.7)          # 等一次 0.5s 轮询
        assert lv.index == 1
        assert "完成" in screen._last_text[2]


async def test_new_task_appended_without_disturbing_selection():
    tasks = [_task(1)]
    app = Host()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = SubAgentListScreen(lambda: tasks)
        await app.push_screen(screen)
        await pilot.pause()
        lv = screen.query_one(ListView)
        lv.index = 0
        tasks.append(_task(2, status="running", role="reviewer"))
        await pilot.pause(0.7)
        assert len(lv.children) == 2
        assert lv.index == 0
        assert "reviewer" in screen._last_text[2]


async def test_f3_on_transcript_does_not_stack_another_list():
    app = Host()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = SubAgentListScreen(lambda: [_task(1)])
        await app.push_screen(screen)
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, SubAgentTranscriptScreen)
        await pilot.press("f3")
        await pilot.pause()
        # 轨迹屏没有 F3 绑定；不得再叠一层任务列表
        assert isinstance(app.screen, SubAgentTranscriptScreen)


async def test_running_task_shows_placeholder():
    app = Host()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = SubAgentTranscriptScreen(_task(1, status="running"))
        await app.push_screen(screen)
        await pilot.pause()
        texts = "\n".join(_line_texts(screen.query_one(RichLog)))
        assert "运行中" in texts


async def test_failed_task_shows_error():
    task = _task(1, status="failed", summary="", error="boom",
                 entries=[TranscriptEntry("assistant", "做到一半")])
    app = Host()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = SubAgentTranscriptScreen(task)
        await app.push_screen(screen)
        await pilot.pause()
        texts = "\n".join(_line_texts(screen.query_one(RichLog)))
        assert "做到一半" in texts and "失败：boom" in texts


async def test_transcript_renders_markup_literally():
    task = _task(1, entries=[TranscriptEntry("user", "[red]不是标记[/red]")])
    app = Host()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = SubAgentTranscriptScreen(task)
        await app.push_screen(screen)
        await pilot.pause()
        texts = "\n".join(_line_texts(screen.query_one(RichLog)))
        assert "[red]不是标记[/red]" in texts


async def test_open_transcript_for_running_then_complete_refreshes():
    """运行中打开轨迹屏，任务完成后自动出现内容。"""
    task = _task(1, status="running", entries=[])
    app = Host()
    async with app.run_test(size=(120, 40)) as pilot:
        screen = SubAgentTranscriptScreen(task)
        await app.push_screen(screen)
        await pilot.pause()
        assert "运行中" in "\n".join(_line_texts(screen.query_one(RichLog)))
        task.status = "done"
        task.entries = tuple(_done_entries())
        await pilot.pause(0.7)
        texts = "\n".join(_line_texts(screen.query_one(RichLog)))
        assert "结论：通过" in texts
