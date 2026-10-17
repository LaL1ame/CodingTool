"""state17: 后台子任务结果回灌主对话的协议安全测试。

- 流式中完成 → 入队，收尾时才进 _msgs（不插进 tool_call/tool_result 之间）。
- 末条已是 user → 合并，避免 Anthropic 连续同角色。
"""

from types import SimpleNamespace

from codepilot.ui.app import CodePilotApp


class _FakeChat:
    def __init__(self, msgs):
        self._msgs = msgs


class _FakeLog:
    def __init__(self):
        self.lines = []

    def write(self, text):
        self.lines.append(text)


def _app(msgs, *, streaming=False):
    app = object.__new__(CodePilotApp)
    app._chat = _FakeChat(msgs)
    app._streaming = streaming
    app._pending_subagent = []
    app._log = _FakeLog()
    app.query_one = lambda *a, **k: app._log
    return app


def _task(status="done", summary="结论", usage=None, role="architect", task_id=1):
    return SimpleNamespace(task_id=task_id, role=role, status=status,
                           summary=summary, error="boom" if status == "failed" else "",
                           usage=usage, created_at=0.0)


def test_append_new_user_message():
    msgs = [{"role": "assistant", "content": "hi"}]
    app = _app(msgs)
    app._append_subagent_summary("摘要")
    assert msgs[-1] == {"role": "user", "content": "摘要"}


def test_merge_into_existing_user_str():
    msgs = [{"role": "user", "content": "已有"}]
    app = _app(msgs)
    app._append_subagent_summary("摘要")
    assert len(msgs) == 1
    assert msgs[0]["content"] == "已有\n\n摘要"


def test_merge_into_existing_user_blocks():
    msgs = [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": "r"}]}]
    app = _app(msgs)
    app._append_subagent_summary("摘要")
    assert len(msgs) == 1
    assert msgs[0]["content"][0] == {"type": "text", "text": "摘要"}
    assert msgs[0]["content"][1]["type"] == "tool_result"


def test_done_while_streaming_queues():
    """流式中完成：写系统行，但摘要入队不进 _msgs（防破坏协议序列）。"""
    msgs = []
    app = _app(msgs, streaming=True)
    app._on_subagent_done(_task())
    assert msgs == []
    assert app._pending_subagent == ["【子任务 #1（architect）完成】\n结论"]
    assert app._log.lines  # 系统行已写


def test_done_idle_appends():
    msgs = []
    app = _app(msgs)
    app._on_subagent_done(_task())
    assert msgs[-1]["role"] == "user"
    assert "结论" in msgs[-1]["content"]


def test_done_displays_summary_in_chat():
    """F11：后台摘要直接写聊天区（A 行为），无需再发一轮。"""
    msgs = []
    app = _app(msgs)
    app._on_subagent_done(_task(summary="架构评审通过"))
    joined = "\n".join(app._log.lines)
    assert "子任务 #1" in joined and "结论" in joined
    assert "架构评审通过" in joined
    # 同时仍进入主对话上下文
    assert "架构评审通过" in msgs[-1]["content"]


def test_summary_markup_escaped_in_chat():
    """模型输出里的 [...] 不能被当作 Rich 标记解析。"""
    app = _app([])
    app._on_subagent_done(_task(summary="[red]伪标记[/red]"))
    joined = "\n".join(app._log.lines)
    assert "\\[red]" in joined


def test_failed_does_not_append():
    msgs = []
    app = _app(msgs)
    app._on_subagent_done(_task(status="failed"))
    assert msgs == []
    assert "失败" in app._log.lines[-1]
