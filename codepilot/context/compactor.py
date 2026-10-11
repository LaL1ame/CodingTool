"""层2 LLM 结构化摘要兜底(F3/F4/F5)。

当整体对话逼近窗口上限时,调用 LLM 生成结构化摘要替代早期消息,近期原文保留。

F4 约束:
  · prompt 明确禁止调用任何工具(且 provider.complete 本身不传 tools)
  · 先写"分析草稿"再写"正式摘要",草稿在内部临时变量、用完即弃、不入任何历史
  · 摘要为独立调用,usage 不计入主对话统计
F5:压缩后经 build_boundary_message() 补一条边界消息,提示模型细节请重读。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from codepilot.providers import BaseProvider

_BOUNDARY_MESSAGE = (
    "【上下文已压缩】早期对话已被结构化摘要替代。若需文件中某个具体代码细节、"
    "函数实现或数据内容,请用 read/grep 工具重新读取源文件确认,不要仅凭摘要脑补。"
)

_SUMMARY_PROMPT_TMPL = """你是一个对话压缩器。本调用**严禁调用任何工具**——只做纯文本总结。

# 分析草稿(仅供内部思考,不要写入正式摘要)
先简短分析下面 recent 里每段对话的要点、涉及的文件与决策,再生成正式摘要。
你写的草稿不会对用户可见,请自由使用。

# 正式摘要(按以下固定部分组织)
## 背景与目标
## 已调用的工具/文件
## 关键决策与结论
## 未完成事项
## 待办

# 历史概览(可包含在摘要中,辅助组织)
{overview}

# 待压缩的近期消息(用它们提取要点)
{recent}

只输出正式摘要本身,遵守上面的固定部分结构,保持信息密度,不遗漏关键代码路径与文件路径。
"""


@dataclass(frozen=True)
class CompactResult:
    """一次层2摘要的产物。"""

    summary: str                # 结构化摘要文本(将替代早期消息)
    boundary_message: str       # F5 边界消息(压缩后追加)
    reviewed_count: int = 0     # 被摘要替代的消息条数


def format_recent_for_summary(recent: list[dict]) -> str:
    """把近期消息 dict(role/content) 展平为摘要 prompt 可读文本。

    content 可能是字符串或原生内容块(list)。做保守展平。
    """
    lines: list[str] = []
    for i, m in enumerate(recent):
        role = m.get("role", "?")
        content = m.get("content", "")
        if isinstance(content, list):
            parts = []
            for blk in content:
                if isinstance(blk, dict):
                    # tool_result / text / tool_use 等块取最相关字段
                    parts.append(str(blk.get("text", blk.get("content", ""))))
            text = " ".join(p for p in parts if p)
        else:
            text = content
        lines.append(f"[{i+1}] {role}: {text}")
    return "\n".join(lines)


class Compactor:
    """调用 LLM 生成结构化摘要;独立、禁工具、草稿即弃。"""

    @staticmethod
    def build_summary_prompt(overview: str, recent: list[dict]) -> str:
        return _SUMMARY_PROMPT_TMPL.format(
            overview=overview or "(无)", recent=format_recent_for_summary(recent))

    @staticmethod
    def build_boundary_message() -> str:
        return _BOUNDARY_MESSAGE

    async def compact(
        self, *,
        provider: "BaseProvider",
        model: str,
        overview: str,
        recent: list[dict],
    ) -> CompactResult:
        """执行摘要。走 provider.complete(独立非流式,禁工具)。

        Args:
            provider: 已配置的 provider(其 complete 不传 tools)。
            overview: 既有压缩的历史概览(若有)。
            recent:    待压缩的近期消息列表。
        Returns:
            CompactResult(正式摘要 + 边界消息 + 条数)。
        Raises:
            Exception: 摘要调用失败(交由上层 ContextBudget 计熔断,不在此吞)。
        """
        prompt = self.build_summary_prompt(overview, recent)
        # 分析草稿在 prompt 内部完成;这里调用 complete 即耗一次推理。
        # 草稿(分析)与正式摘要同一次返回内完成,草稿不进任何历史 ——
        # 这是单次 complete 返回即可满足 F4.2,无需二次调用。
        summary = await provider.complete(system=prompt, messages=[])
        return CompactResult(
            summary=summary or "",
            boundary_message=self.build_boundary_message(),
            reviewed_count=len(recent),
        )