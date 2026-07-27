"""输入框 —— 多行编辑 + 流式锁定。"""

from textual.binding import Binding
from textual.message import Message
from textual.widgets import TextArea


class InputBox(TextArea):
    """底部多行输入框。

    Enter 提交，Alt+Enter 插入换行。
    流式等待期间禁用输入。
    """

    BINDINGS = [
        Binding("enter", "submit", "发送消息", show=False, priority=True),
        Binding("alt+enter", "insert_newline", "插入换行", show=False),
    ]

    class Submitted(Message):
        """消息提交事件。"""

        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    def __init__(self) -> None:
        super().__init__(text="", language=None, show_line_numbers=False)
        self.border_title = "❯ Send a message..."
        self._locked = False

    @property
    def locked(self) -> bool:
        return self._locked

    def lock(self) -> None:
        """锁定输入框，流式等待期间不接受提交。"""
        self._locked = True
        self.disabled = True

    def unlock(self) -> None:
        """解锁输入框。"""
        self._locked = False
        self.disabled = False

    def action_submit(self) -> None:
        """Enter：提交消息。"""
        if self._locked:
            return
        text = self.text
        if text.strip():
            self.post_message(self.Submitted(text))
        self.clear()

    def action_insert_newline(self) -> None:
        """Alt+Enter：插入换行。"""
        self.insert("\n")
