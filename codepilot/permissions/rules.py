"""规则模型：Rule 数据类 + 语法解析 + 匹配 + 合并。

规则声明为 `Tool(pattern)`，例如 `run(git *)` → tool="run", pattern="git *"。
结果为 allow / ask / deny。判定时 deny > ask > allow，与规则写入的文件层级无关。
"""

import fnmatch
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


def match_text(pattern: str, subject: str | None) -> bool:
    """规则 pattern 是否命中 subject。

    无通配符时按精确匹配；含 glob 通配时用 fnmatch（支持 * ? []）。
    """
    if subject is None:
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