"""load_skill 工具 — 阶段二按需加载入口（system_exempt，不受白名单约束）。

模型在对话中需要某个 Skill 的完整 SOP 时调用；激活后下轮起完整指令钉住、
对应白名单生效。返回渲染后的完整指令作为本次工具结果（当场可见）。
"""

from __future__ import annotations

from codepilot.protocols import ToolResult
from codepilot.skills.manager import SkillManager
from codepilot.skills.model import SkillError
from codepilot.tools.base import Tool


class LoadSkillTool(Tool):
    def __init__(self, manager: SkillManager) -> None:
        self._manager = manager

    @property
    def name(self) -> str:
        return "load_skill"

    @property
    def description(self) -> str:
        return (
            "加载并激活一个 Skill（可复用 AI 操作），返回其完整 SOP 指令。"
            "调用后该 Skill 的完整指令会钉在当前对话、并按需收窄可用工具。"
            "参数 name 填 Skill 唯一名（可用 /help 或提示词里的可用技能列表查到）。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "要加载的 Skill 唯一名。"},
                "params": {"type": "string", "default": "",
                           "description": "传给 SOP 正文占位符 {input} 的用户内容（可选）。"},
            },
            "required": ["name"],
        }

    @property
    def system_exempt(self) -> bool:
        # 系统级工具：逃生门。任何 Skill 白名单下都保持可见可调，防止被锁死。
        return True

    @property
    def side_effect(self) -> bool:
        # 会改变会话激活态（钉住 SOP/白名单），当作副作用串行执行，避免并发加载错乱。
        return True

    async def execute(self, name: str, params: str = "") -> ToolResult:
        try:
            rendered = self._manager.load(name, params)
        except SkillError as e:
            return ToolResult(call_id="", name=self.name, error=str(e))
        return ToolResult(call_id="", name=self.name, output=rendered)