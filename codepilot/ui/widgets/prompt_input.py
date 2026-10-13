"""多行输入框 PromptInput —— 覆写 TextArea._on_key 区分「发送/换行」。

TextArea 在内部 `_on_key` 把 enter 直接映射为插换行（声明式 BINDINGS 拦不住）。
这里翻转为用户偏好：
  enter     → 触发 Submitted 消息（App 统一发送）
  ctrl+j    → 插入换行（LF 控制符；实机验证本机 Windows 终端把 Enter 的修饰组合
              全折叠成 enter，唯独 ctrl+j 这类控制字符独立送达）
其余按键（可打印/退格/方向等）走基类编辑语义。
"""
from __future__ import annotations

from collections.abc import Callable

from textual.events import Key
from textual.message import Message
from textual.widgets import TextArea

# 换行键：仅 ctrl+j（LF 控制符）。此前加入的 ctrl/alt/shift+enter 在本机终端全被折叠成
# enter(等于发送)，已按实机结论移除，避免「想换行却发出去了」。
_NEWLINE_KEYS = frozenset({"ctrl+j"})


class PromptInput(TextArea):
    """Enter=发送、Ctrl+J=换行的多行输入框；支持 Tab 命令补全(state14)。"""

    class Submitted(Message):
        """用户按 enter 确认发送。App 据此读取文本并调 _submit 分发。"""

    class CompletionRequested(Message):
        """Tab 多命中：请求 App 弹补全菜单（携候选与当前前缀）。"""

        def __init__(self, candidates: list[str], prefix: str) -> None:
            super().__init__()
            self.candidates = candidates
            self.prefix = prefix

    def __init__(self, completer: Callable[[str], list[str]] | None = None,
                 **kwargs) -> None:
        super().__init__(**kwargs)
        self._completer = completer

    def _command_prefix(self) -> str | None:
        """当行首为斜杠时返回当前补全前缀('/' 后至首空白前的 token),否则 None。"""
        text = self.text
        if not text.lstrip().startswith("/"):
            return None
        stripped = text.lstrip()
        return stripped[1:].split(" ", 1)[0]

    def apply_completion(self, name: str) -> None:
        """把当前 '/' 前缀替换为 '/<name> '(由补全菜单选中回调)。"""
        prefix = self._command_prefix()
        if prefix is None:
            return
        text = self.text
        slash = text.find("/")
        # 替换 '/' 之后的前缀段
        rest = text[slash + 1:]
        if len(rest) >= len(prefix) and rest.startswith(prefix):
            new = text[:slash + 1] + name + " " + text[slash + 1 + len(prefix):]
        else:
            new = text[:slash] + "/" + name + " "
        self.text = new

    async def _on_key(self, event: Key) -> None:
        if event.key == "tab":
            event.stop()
            event.prevent_default()
            self._handle_tab()
            return
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

    def _handle_tab(self) -> None:
        if self._completer is None:
            return
        prefix = self._command_prefix()
        if prefix is None:
            return
        cands = self._completer(self.text)
        if not cands:
            return
        if len(cands) == 1:
            self.apply_completion(cands[0])
        else:
            self.post_message(self.CompletionRequested(cands, prefix))