"""规则模型：Rule 数据类 + 语法解析 + 匹配 + 合并。

规则声明为 `Tool(pattern)`，例如 `run(git *)` → tool="run", pattern="git *"。
结果为 allow / ask / deny。判定时 deny > ask > allow，与规则写入的文件层级无关。
"""

import fnmatch
import re
from dataclasses import dataclass
from enum import Enum


class Verdict(Enum):
    """一条规则的判定结果。"""
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


_VERDICT_TEXT = {
    "allow": Verdict.ALLOW,
    "ask": Verdict.ASK,
    "deny": Verdict.DENY,
}


@dataclass
class Rule:
    """一条权限规则。pattern 按 fnmatch 语义匹配该工具的 subject。"""
    tool: str
    pattern: str
    verdict: Verdict


def parse_rule(text: str) -> Rule:
    """解析 `Tool(pattern)` 字符串为 Rule。结果三段用冒号分隔：`run(git *): allow`。

    Example:
        parse_rule("run(git *): allow")  -> Rule("run", "git *", Verdict.ALLOW)
    """
    head, _, rest = text.partition(":")  # 结果段（可省略，默认 allow）
    open_idx = head.find("(")
    close_idx = head.find(")")
    if open_idx < 0 or close_idx < 0 or close_idx < open_idx:
        raise ValueError(f"规则格式错误，缺少 (pattern): {text!r}")
    tool = head[:open_idx].strip()
    pattern = head[open_idx + 1:close_idx].strip()
    verdict = verdict_from_text(rest.strip())
    return Rule(tool=tool, pattern=pattern, verdict=verdict)


def verdict_from_text(text: str) -> Verdict:
    """把 allow/ask/deny 文本解析为 Verdict。空/未知默认 allow。"""
    return _VERDICT_TEXT.get(text.strip().lower(), Verdict.ALLOW)


def is_regex_pattern(pattern: str) -> bool:
    """pattern 是否为 `/.../` 正则形态（首尾均 / 且长度>=2）。"""
    return len(pattern) >= 2 and pattern.startswith("/") and pattern.endswith("/")


def regex_body(pattern: str) -> str:
    """取出 `/.../` 包裹的正则正文（调用前须 is_regex_pattern 为真）。"""
    return pattern[1:-1]


def match_text(pattern: str, subject: str | None) -> bool:
    """规则 pattern 是否命中 subject。四种语法（权限规则与 Hook 条件共用）：

    - `!` 前缀   → 反向（可包任意下层语法：!git * / !/rm -rf/ / !exact）
    - `/.../`    → 正则 re.search；非法正则防御性 no-match（加载期另行校验）
    - 含 * ? [   → glob（fnmatch）
    - 其余       → 精确相等
    """
    if subject is None:
        return False
    if pattern.startswith("!"):
        return not match_text(pattern[1:], subject)
    if is_regex_pattern(pattern):
        try:
            return re.search(regex_body(pattern), subject) is not None
        except re.error:
            return False
    if any(ch in pattern for ch in "*?["):
        return fnmatch.fnmatch(subject, pattern)
    return pattern == subject


def merge(rule_lists: list[list[Rule]]) -> list[Rule]:
    """把多来源规则并成一套。判定与来源无关，由 policy 统一做优先级。"""
    result: list[Rule] = []
    seen: set[tuple[str, str, Verdict]] = set()
    for rules in rule_lists:
        for r in rules:
            key = (r.tool, r.pattern, r.verdict)
            if key not in seen:
                seen.add(key)
                result.append(r)
    return result