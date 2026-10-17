"""子 Agent 任务面板（F11 增量）。

- `SubAgentListScreen`：F3 打开的任务列表。ListView 原生 ↑↓/Enter；
  0.5s 轮询原地更新行（保住选中项），Enter 推入轨迹屏，Esc 关闭。
- `SubAgentTranscriptScreen`：只读轨迹（用户/助手/工具调用/工具结果）；
  任务完成后自动刷新；Esc 返回列表。

轨迹条目用 `rich.text.Text` 渲染（markup=False），用户/模型文本中的 `[...]`
不会被当作 Rich 标记解析。
"""

from __future__ import annotations

import time
from collections.abc import Callable

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Label, ListItem, ListView, RichLog, Static

from codepilot.subagents.transcript import TranscriptEntry

_POLL_S = 0.5
_ROW_PROMPT_MAX = 44
_ACTIVE = ("running", "backgrounded")

_STATUS_LABEL = {
    "running": "运行中…",
    "backgrounded": "后台运行…",
    "done": "完成",
    "failed": "失败",
    "cancelled": "已取消",
}


def _excerpt(text: str, limit: int = _ROW_PROMPT_MAX) -> str:
    flat = " ".join((text or "").split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1] + "…"


def _role_label(task) -> str:
    if getattr(task, "kind", "") == "fork":
        return "[Fork]"
    return f"[角色] {getattr(task, 'role', None) or '-'}"


def _elapsed(task, now: float) -> float:
    created = getattr(task, "created_at", 0.0) or 0.0
    return max(0.0, now - created) if created else 0.0


def _usage_text(task) -> str:
    usage = getattr(task, "usage", None)
    return f" · {usage.input_tokens}t" if usage else ""


def format_task_row(task, now: float | None = None) -> str:
    """任务列表一行（纯文本，供 ListItem 的 Text 渲染）。"""
    now = time.time() if now is None else now
    status = _STATUS_LABEL.get(getattr(task, "status", ""), getattr(task, "status", ""))
    return (f"#{task.task_id} {_role_label(task)} · {status} · "
            f"{_elapsed(task, now):.1f}s{_usage_text(task)} · "
            f"{_excerpt(getattr(task, 'prompt', ''))}")


def format_task_header(task, now: float | None = None) -> str:
    """轨迹屏标题。"""
    now = time.time() if now is None else now
    status = _STATUS_LABEL.get(getattr(task, "status", ""), getattr(task, "status", ""))
    count = len(getattr(task, "entries", ()) or ())
    return (f"#{task.task_id} {_role_label(task)} · {status} · "
            f"{_elapsed(task, now):.1f}s{_usage_text(task)} · {count} 条记录")


def _entry_text(entry: TranscriptEntry) -> Text:
    """一条轨迹的带色渲染文本（不经过 markup 解析）。"""
    text = Text()
    if entry.kind == "user":
        text.append("❯ 用户  ", style="bold green")
        text.append(entry.text)
    elif entry.kind == "assistant":
        text.append("🤖 助手  ", style="bold cyan")
        text.append(entry.text)
    elif entry.kind == "tool_use":
        text.append(f"🔧 {entry.name or 'tool'}  ", style="magenta")
        text.append(entry.text, style="dim")
    else:
        text.append("↳ ", style="bold red" if entry.is_error else "dim")
        text.append(entry.text, style="red" if entry.is_error else "")
    return text


class SubAgentTranscriptScreen(ModalScreen[None]):
    """单个子任务的只读轨迹视图。"""

    BINDINGS = [("escape", "back", "返回列表")]

    def __init__(self, task) -> None:
        super().__init__()
        self._subagent_task = task
        self._last_key: tuple[str, int] | None = None
        self._timer = None

    def compose(self) -> ComposeResult:
        with Vertical(id="transcript-box"):
            yield Static(Text(format_task_header(self._subagent_task)), id="transcript-title")
            yield RichLog(id="transcript-log", markup=False, wrap=True,
                          auto_scroll=False, highlight=False)
            yield Static("Esc 返回 · ↑↓/PgUp/PgDn 滚动", id="transcript-hint")

    def on_mount(self) -> None:
        self._render_transcript()
        self.query_one("#transcript-log", RichLog).focus()
        self._timer = self.set_interval(_POLL_S, self._tick)

    def on_unmount(self) -> None:
        if self._timer is not None:
            self._timer.stop()

    def action_back(self) -> None:
        self.dismiss(None)

    def _entries(self) -> tuple[TranscriptEntry, ...]:
        return tuple(getattr(self._subagent_task, "entries", ()) or ())

    def _tick(self) -> None:
        """任务状态/条目变化时重绘（运行中完成后自动出现完整轨迹）。"""
        key = (getattr(self._subagent_task, "status", ""), len(self._entries()))
        if key == self._last_key:
            return
        try:
            self._render_transcript()
        except NoMatches:
            return

    def _render_transcript(self) -> None:
        self._last_key = (getattr(self._subagent_task, "status", ""), len(self._entries()))
        log = self.query_one("#transcript-log", RichLog)
        log.clear()
        entries = self._entries()
        if not entries:
            status = getattr(self._subagent_task, "status", "")
            if status in _ACTIVE:
                log.write(Text("运行中…（完成后显示完整过程）", style="dim"))
            else:
                log.write(Text("（无过程记录）", style="dim"))
        for entry in entries:
            log.write(_entry_text(entry))
        status = getattr(self._subagent_task, "status", "")
        if status == "failed":
            error = getattr(self._subagent_task, "error", "") or "未知错误"
            log.write(Text(f"失败：{error}", style="bold red"))
        elif status == "cancelled":
            log.write(Text("（任务已取消）", style="dim"))
        # 标题随状态刷新
        self.query_one("#transcript-title", Static).update(
            Text(format_task_header(self._subagent_task)))


class SubAgentListScreen(ModalScreen[None]):
    """子 Agent 任务列表（F3）。"""

    BINDINGS = [("escape", "close", "关闭"), ("f3", "close", "关闭")]

    def __init__(self, tasks_provider: Callable[[], list]) -> None:
        super().__init__()
        self._provider = tasks_provider
        self._views: list = []
        self._items: dict[int, ListItem] = {}
        self._last_text: dict[int, str] = {}
        self._timer = None

    def compose(self) -> ComposeResult:
        self._views = list(self._provider())
        items: list[ListItem] = []
        for task in self._views:
            row = format_task_row(task)
            item = ListItem(Label(Text(row)), id=f"task-{task.task_id}")
            self._items[task.task_id] = item
            self._last_text[task.task_id] = row
            items.append(item)
        with Vertical(id="subagent-box"):
            yield Static("子 Agent 任务（↑↓ 选择 · Enter 查看轨迹）",
                         id="subagent-title")
            yield ListView(*items, id="subagent-list")
            yield Static("Esc 关闭 · F3 关闭 · 后台完成会自动刷新",
                         id="subagent-list-hint")

    def on_mount(self) -> None:
        lv = self.query_one("#subagent-list", ListView)
        lv.focus()
        if self._views and lv.index is None:
            lv.index = 0
        self._timer = self.set_interval(_POLL_S, self._poll)

    def on_unmount(self) -> None:
        if self._timer is not None:
            self._timer.stop()

    def action_close(self) -> None:
        self.dismiss(None)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        lv = self.query_one("#subagent-list", ListView)
        idx = lv.index
        if idx is None or not (0 <= idx < len(self._views)):
            return
        self.app.push_screen(SubAgentTranscriptScreen(self._views[idx]))

    def _poll(self) -> None:
        try:
            self._refresh()
        except NoMatches:
            return

    def _refresh(self) -> None:
        """原地更新行（不清空 ListView，选中索引保持不变）。"""
        tasks = list(self._provider())
        lv = self.query_one("#subagent-list", ListView)
        now = time.time()
        for task in tasks:
            row = format_task_row(task, now)
            item = self._items.get(task.task_id)
            if item is None:
                item = ListItem(Label(Text(row)), id=f"task-{task.task_id}")
                self._items[task.task_id] = item
                self._last_text[task.task_id] = row
                lv.mount(item)
            elif self._last_text.get(task.task_id) != row:
                item.query_one(Label).update(Text(row))
                self._last_text[task.task_id] = row
        self._views = tasks
        if self._views and lv.index is None:
            lv.index = 0
