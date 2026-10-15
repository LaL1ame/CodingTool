"""Hook 规则数据模型：三要素（event + if + action）的不可变结构。

纯数据，零依赖。8 事件常量 + 字段注册表（校验用）也放这里。
"""

from dataclasses import dataclass


class HookError(Exception):
    """Hook 规则加载/校验错误 —— 启动 fail-fast。"""


# 8 个生命周期事件（字符串即协议）
EVENTS = (
    "session_start", "session_end",
    "round_start",
    "user_message",
    "tool_pre", "tool_post",
    "compact", "stop",
)

# 四种动作类型（subagent 本阶段占位）
ACTION_TYPES = ("command", "prompt", "http", "subagent")

# 唯一可拦截事件（拦截必须同步出结果 → 禁止 async，loader 校验）
INTERCEPT_EVENT = "tool_pre"

# 非 tool 事件的保留条件字段注册表（校验「条件字段对该事件存在」用）。
# tool_pre/tool_post 的条件字段 = 保留字段 ∪ 工具参数键（参数键放开不校验，
# 因为 MCP 工具的参数名无法穷举）。
EVENT_FIELDS: dict[str, set[str]] = {
    "session_start": {"resumed"},
    "session_end": set(),
    "round_start": {"round"},
    "user_message": {"text"},
    "tool_pre": {"tool"},
    "tool_post": {"tool", "ok"},
    "compact": set(),
    "stop": {"reason"},
}


@dataclass(frozen=True)
class CondItem:
    """一条条件：事件字段 + 共享匹配语法 pattern。"""
    field: str
    pattern: str


@dataclass(frozen=True)
class Condition:
    """条件组合：mode 为 all（全部满足）或 any（任一满足），二选一不混用。"""
    mode: str  # "all" | "any"
    items: tuple[CondItem, ...]


@dataclass(frozen=True)
class HookAction:
    """动作：类型 + 原始字段 + 执行控制三选项。"""
    type: str                       # command | prompt | http | subagent
    fields: dict                    # 动作原始字段（cmd/text/url/...）
    timeout: float | None = None    # None → engine 用全局默认
    is_async: bool = False
    once: bool = False


@dataclass(frozen=True)
class HookRule:
    """一条 Hook 规则：event + 可选 condition + action + 来源定位。"""
    event: str
    condition: Condition | None     # None = 无条件触发
    action: HookAction
    source: str                     # 来源文件路径（日志/排错/once 键）
    ordinal: int                    # 文件内序号（once 键、声明顺序的键）
