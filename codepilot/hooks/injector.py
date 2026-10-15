"""HookInjector —— prompt 动作的 volatile 块仓库。

与 memory/skill 注入缝同构：Agent 每轮调 hook_blocks() 拿到注入块，
拼进 system 动态尾块。同 key 覆盖即去重；/clear 时 clear() 复位。
"""


class HookInjector:
    def __init__(self) -> None:
        # dict 保插入序；key = 规则标识（source#ordinal）
        self._blocks: dict[str, str] = {}

    def add(self, key: str, text: str) -> None:
        """登记/覆盖一条注入块。同 key 覆盖 → 同一规则重复触发只钉一份。"""
        self._blocks[key] = text

    def hook_blocks(self) -> list[str]:
        """Agent 每轮回调：当前全部注入块（插入序）。空 → []。"""
        return list(self._blocks.values())

    def clear(self) -> None:
        """/clear 联动：清空全部注入块。"""
        self._blocks.clear()
