"""上下文管理编排入口(T8)。

ContextManager 聚合 estimator/budget/offloader/compactor/provider/chat,对外
提供会话级 API。触发时机:
  · send_message 前:pre_request() → 层1批(本消息工具) + 层2判断(累积历史)
  · agent 每轮工具结果回灌前:prep_tool_result() → 层1单结果存盘
  · UI:TUI 上下文指示器经 current_usage_text() 展示

compaction 作用在 chat._msgs(LLM 真正看到的序列)。通过把摘要+边界消息
前置进保留区第一条消息的内容块,保持 Anthropic 的角色交替合法,不新增
连续同角色消息。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from codepilot.context.budget import ContextBudget, ContextConfig
from codepilot.context.compactor import CompactResult, Compactor
from codepilot.context.estimator import TokenEstimator
from codepilot.context.offload import OffloadedResult, ToolResultOffloader

if TYPE_CHECKING:
    from codepilot.chat import ChatManager
    from codepilot.providers import BaseProvider
    from codepilot.protocols import ToolResult

_HEADER = "【历史摘要】"

def _make_text_block(text: str) -> dict:
    return {"type": "text", "text": text}


def _prepend_text_to_message(msg: dict, text: str) -> dict:
    """把文本前置进消息 content。content 为 list → 前插 text 块;
    content 为 str → 合并为字符串;缺失 → 建 list。返回新消息(不就地改)。"""
    content = msg.get("content")
    if isinstance(content, list):
        return {**msg, "content": [_make_text_block(text), *content]}
    if isinstance(content, str):
        return {**msg, "content": text + "\n\n" + content}
    return {**msg, "content": [_make_text_block(text)]}


@dataclass
class _Tail:
    head: list[dict]
    tail: list[dict]


class ContextManager:
    def __init__(
        self, *,
        chat: "ChatManager | None" = None,
        provider: "BaseProvider",
        config: ContextConfig | None = None,
        offload_dir: Path | None = None,
    ) -> None:
        self.chat = chat
        self.provider = provider
        self.config = config or ContextConfig()
        self.estimator = TokenEstimator()
        self.budget = ContextBudget(self.config, self.estimator)
        self.offloader = ToolResultOffloader(offload_dir or Path.cwd() / ".codepilot" / "context")
        self.compactor = Compactor()
        self._overview = ""

    def attach_chat(self, chat: "ChatManager") -> None:
        """TUI on_mount 后补绑 chat(main 构建时 chat 尚未创建)。"""
        self.chat = chat

    # —— 层1:单工具结果(prep_tool_result,agent 每轮调用) ——
    # read 豁免(state10 验收修正):read 是我方主动给出、且自带 offset/limit 分页 +
    # READ_LIMIT 行内截断(truncated=True)的自分页工具 —— 它就是 read 的上下文边界机制。
    # 层1存盘会对它造成「预览→按路径重读→再存盘」死循环,故豁免。层1只面向模型
    # 无法分页的不透明大输出(如 run/grep 大块),避免在模型需要的当前内容上截断。
    # 文件读多后的历史老化交给层2摘要(compactor)。
    _SELF_PAGING_TOOLS = frozenset({"read"})

    async def prep_tool_result(self, result: "ToolResult") -> "ToolResult":
        """层1单结果:除 read(自带分页,豁免)外,> N 存盘并返回替身;否则原样返回。"""
        if result.name in self._SELF_PAGING_TOOLS:
            self.estimator.add((result.output or ""), kind="code")
            return result
        off = await self.offloader.offload(result, self.config.tool_threshold_n)
        if off is None:
            # 记录增量(估算口径)
            self.estimator.add((result.output or ""), kind="code")
            return result
        self.estimator.add(off.preview, kind="code")
        self.budget.record_offload(off.saved_tokens)
        return self._to_toolresult(result, off)

    # —— 层1:批(pre_request,本消息工具结果合计) ——
    async def _layer1_batch(self, results: list["ToolResult"]) -> None:
        offs, saved = await self.offloader.offload_oversized(
            results, self.config.tool_batch_threshold_m, self.config.tool_threshold_n)
        if saved:
            self.budget.record_offload(saved)
        # offs 处理结果仅影响本消息 ctx 的构建;这里已记录回收。

    # —— 层2:判断 + 执行 ——
    async def _maybe_compact_auto(self) -> CompactResult | None:
        if not await self.budget.should_compact():
            return None
        return await self._do_compact(manual=False)

    async def compact_manual(self) -> CompactResult | None:
        """F6.1 手动触发(manual_margin 更小)。"""
        # 手动压缩条件:未熔断
        if not self.budget.manual_allowed:
            return None
        return await self._do_compact(manual=True)

    async def _do_compact(self, *, manual: bool) -> CompactResult | None:
        msgs = self.chat._msgs
        tail = self._split_tail(msgs, manual=manual)
        if not tail.head or not tail.tail:
            # 无早期可压 / 全保留 → 不压
            return None
        try:
            result = await self.compactor.compact(
                provider=self.provider, model=self.provider.config.model,
                overview=self._overview, recent=tail.head + tail.tail[:self.config.compact_keep_min_msgs])
            # 改写 _msgs:head 被压缩为(摘要+边界)前置进 tail 首条
            header = _HEADER + result.summary + "\n\n" + result.boundary_message
            tail.tail[0] = _prepend_text_to_message(tail.tail[0], header)
            self.chat._msgs = tail.tail
            self._overview = result.summary
            self.budget.summarize_succeeded()
            self.budget.anchor(self._estimate_msgs())  # 结构已变,重锚估
            return result
        except Exception:
            self.budget.register_summarize_failure()
            raise

    def _split_tail(self, msgs: list[dict], *, manual: bool) -> _Tail:
        """分割为 head(可压) / tail(原文保留)。

        manual=False(自动):保留最近 max(keep_tokens token, keep_min 条) 的更早前
        缀;历史不足该保留量 → 全保留不压(head 空)。
        manual=True(用户强制):只保留最近 keep_min 条,更早全部进 head 压掉。
        """
        if not msgs:
            return _Tail(head=[], tail=[])
        if manual:
            if len(msgs) <= self.config.compact_keep_min_msgs:
                return _Tail(head=[], tail=msgs)
            cut = len(msgs) - self.config.compact_keep_min_msgs
            return _Tail(head=msgs[:cut], tail=msgs[cut:])
        # 自动:从尾部往回,累计到 keep_tokens 或 keep_min 条
        keep_tokens = self.config.compact_keep_tokens
        acc = 0
        split = 0
        for i in range(len(msgs) - 1, -1, -1):
            acc += _est(self._msg_text(msgs[i]))
            if acc >= keep_tokens and (len(msgs) - i) >= self.config.compact_keep_min_msgs:
                split = i
                break
        return _Tail(head=msgs[:split], tail=msgs[split:])

    @staticmethod
    def _msg_text(msg: dict) -> str:
        c = msg.get("content", "")
        if isinstance(c, list):
            parts = [str(b.get("text", b.get("content", ""))) for b in c if isinstance(b, dict)]
            return " ".join(p for p in parts)
        return c or ""

    def _estimate_msgs(self) -> int:
        return sum(_est(self._msg_text(m)) for m in self.chat._msgs)

    @staticmethod
    def _to_toolresult(original: "ToolResult", off: OffloadedResult) -> "ToolResult":
        # 用一个轻量回报对象——用 ToolResult 的 output 装 display_text
        from codepilot.protocols import ToolResult
        return ToolResult(
            call_id=original.call_id, name=original.name,
            output=off.display_text(), error=original.error,
            truncated=original.truncated, total_items=original.total_items,
        )

    # —— 对外 API ——
    async def pre_request(self) -> CompactResult | None:
        """send_message 前:层2判断(累积历史)。层1批由 agent 每轮 prep 承担。"""
        return await self._maybe_compact_auto()

    def on_usage_anchor(self, input_tokens: int) -> None:
        """每轮 usage 重锚(LLM 亲测权威值)。"""
        self.budget.anchor(input_tokens)

    def reset_circuit(self) -> None:
        self.budget.reset_circuit()

    def current_usage_text(self, *, manual: bool = False) -> str:
        """F7 TUI 显示串:已用/窗口。manual 只影响格式(余量说明已由触发体现)。"""
        occ = self.budget.effective_occupied
        win = self.config.window
        pct = f"{occ * 100 // win}%" if win else "0%"
        circuit = " ⚠熔断" if self.budget.is_openckety else ""
        return f"ctx {occ//1000}K/{win//1000}K ({pct}){circuit}"


def _est(text: str) -> int:
    from codepilot.context.estimator import estimate_text
    return estimate_text(text, kind="code")