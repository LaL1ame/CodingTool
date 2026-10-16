"""ChatLog —— 跟随式 RichLog(滚动健康修复)。

根因(实证):Textual 8.2.8 的 RichLog.write 在 auto_scroll=True 时每次调用
`scroll_end(immediate=False)` —— 延迟到下次刷新才落地。流式持续写入时,
总会有一个「上次位于底部时排队的延迟滚动」在用户上滚后才落地,把视图拽回
底部 → 「生成中无法向上翻历史」。

本类方案:关闭内建 auto_scroll,改为写前判断——
- 写入前用户位于底部 → 同步跟随(scroll_end immediate=True,无延迟排队)
- 写入前用户已上滚 → 完全不滚,位置保持
判断基于写前位置而非事件来源,鼠标滚轮/滚动条/键盘任意上滚方式全覆盖。

注意:RichLog 内部 on_resize 会用位置参数重放 write(content, width,
expand, shrink, scroll_end),故本覆写显式列出完整签名而非 kwargs。
"""

from __future__ import annotations

from textual.widgets import RichLog


class ChatLog(RichLog):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # 内建 auto_scroll 用 immediate=False 延迟滚动,是拽回根因 → 关闭,自行跟随
        self.auto_scroll = False

    def write(self, content, width=None, expand=False, shrink=True,
              scroll_end=None, animate=False):
        prev_max = self.max_scroll_y
        # 精确底部(容忍仅浮点漂移):上滚 1 行即视为「用户已离开底部」→ 不再跟随
        was_at_bottom = prev_max <= 1 or self.scroll_y >= prev_max - 0.01
        result = super().write(content, width=width, expand=expand, shrink=shrink,
                               scroll_end=False, animate=animate)
        if was_at_bottom:
            # 位于底部 → 同步跟随到新底部(immediate=True 不经延迟,无待落地的拽回)
            self.scroll_end(animate=animate, immediate=True, x_axis=False)
        return result
