"""多行输入框 PromptInput —— 覆写 TextArea._on_key 区分「发送/换行」。

TextArea 在内部 `_on_key` 把 enter 直接映射为插换行（声明式 BINDINGS 拦不住）。
这里翻转为用户偏好：
  enter     → 触发 Submitted 消息（App 统一分发送消息）
  ctrl+enter、alt+enter、shift+enter → 插入换行（多终端兼容：部分 Windows
  终端不把 Ctrl+Enter 作为独立键送出，落在 Shift/Alt 上更可靠）
其余按键（可打印/退格/方向等）走基类编辑语义。
"""
from __future__ import annotations

from textual.events import Key
from textual.message import Message
from textual.widgets import TextArea

# 换行触发键：Textual 对 combo enter 给出 key='ctrl+enter'/'alt+enter'/'shift+enter'。
# 兼容不同终端对同序列的送达差异（如部分终端只把 Ctrl 送成 Shift）。
_NEWLINE_KEYS = frozenset({"ctrl+enter", "alt+enter", "shift+enter"})


class PromptInput(TextArea):
    """Enter=发送、Ctrl/Alt/Shift+Enter=换行的多行输入框。"""

    class Submitted(Message):
        """用户按 enter 确认发送。App 据此读取文本并调 _submit 分发。"""

    async def _on_key(self, event: Key) -> None:
        if event.key == "enter":
            # 发送：吞掉事件（不插换行），通知 App。
            event.stop()
            event.prevent_default()
            self.post_message(self.Submitted())
            return
        if event.key in _NEWLINE_KEYS:
            # 换行：镜像基类对 enter 的处理（选中区替换为换行）。
            event.stop()
            event.prevent_default()
            start, end = self.selection
            self._replace_via_keyboard("\n", start, end)
            return
        # 其余按键：委托基类（打字/退格/移动光标/复制粘贴等）。
        await super()._on_key(event)