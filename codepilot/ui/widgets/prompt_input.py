"""多行输入框 PromptInput —— 覆写 TextArea._on_key 区分「发送/换行」。

TextArea 在内部 `_on_key` 把 enter 直接映射为插换行（声明式 BINDINGS 拦不住）。
这里翻转为用户偏好：
  enter     → 触发 Submitted 消息（App 统一发送）
  ctrl+j    → 插入换行（LF 控制符；实机验证本机 Windows 终端把 Enter 的修饰组合
              全折叠成 enter，唯独 ctrl+j 这类控制字符独立送达）
其余按键（可打印/退格/方向等）走基类编辑语义。
"""
from __future__ import annotations

from textual.events import Key
from textual.message import Message
from textual.widgets import TextArea

# 换行键：仅 ctrl+j（LF 控制符）。此前加入的 ctrl/alt/shift+enter 在本机终端全被折叠成
# enter(等于发送)，已按实机结论移除，避免「想换行却发出去了」。
_NEWLINE_KEYS = frozenset({"ctrl+j"})


class PromptInput(TextArea):
    """Enter=发送、Ctrl+J=换行的多行输入框。"""

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