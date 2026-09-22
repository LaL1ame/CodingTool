"""PermissionManager — 五层防御裁决引擎。

判定顺序（任一 deny 则拒绝，语义 hard）：
    1. 黑名单（仅 run 命令）      → 硬 deny
    2. 路径沙箱（文件工具）        → 硬 deny
    3. 规则命中                   → deny > ask > allow
    4. 无命中 → 模式兜底           → strict=deny / default=ask / permissive=allow
    5. ask → 人机回环 → 本次/会话/永久/拒绝
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import yaml

from codepilot.permissions.blacklist import hit as blacklist_hit
from codepilot.permissions.mode import PermissionMode, fallback_for
from codepilot.permissions.rules import Rule, Verdict, match_text
from codepilot.permissions.sandbox import check_escape

logger = logging.getLogger("codepilot.permissions")

# 工具 → 规则匹配所取参数（subject）
SUBJECT_KEYS: dict[str, str] = {
    "read": "file_path",
    "write": "file_path",
    "edit": "file_path",
    "run": "command",
    "glob": "pattern",
    "grep": "pattern",
}


class HumanChoice(Enum):
    """人在回路给同一工具调用的四种决定。"""
    ALLOW_THIS = "this"
    ALLOW_SESSION = "session"
    ALLOW_FOREVER = "forever"
    DENY = "deny"


# 人机回环回调类型：(tool_name, subject) → HumanChoice
HumanCallback = Callable[[str, str], Awaitable[HumanChoice]]


@dataclass
class Decision:
    """一次工具判定的结果。"""
    allow: bool
    reason: str = ""
    auto: bool = False  # True=因 accept-edits / auto 模式自动放行（未弹权限框）


class PermissionManager:
    def __init__(
        self,
        workspace: Path,
        mode: PermissionMode = PermissionMode.DEFAULT,
        rules: list[Rule] | None = None,
        human_callback: HumanCallback | None = None,
        local_rules_path: Path | None = None,
    ) -> None:
        self.workspace = workspace
        self.mode = mode
        self.rules: list[Rule] = rules or []
        self.human_callback = human_callback
        self.local_rules_path = local_rules_path
        self._session_cache: set[tuple[str, str]] = set()
        self.accept_edits = False  # 自动编辑：write/edit 自动应用，run 仍走确认

    def set_mode(self, mode: PermissionMode) -> None:
        self.mode = mode

    def set_accept_edits(self, on: bool) -> None:
        """开启后 write/edit 的 ASK 判定自动放行（不弹确认框）。"""
        self.accept_edits = on

    def _subject(self, tool: str, arguments: dict) -> str | None:
        key = SUBJECT_KEYS.get(tool)
        if key is None:
            return None
        val = arguments.get(key)
        return val if isinstance(val, str) else None

    def _rule_verdict(self, tool: str, subject: str | None) -> Verdict | None:
        """规则层：deny > ask > allow。无命中返回 None。"""
        deny = ask = allow = False
        for r in self.rules:
            if r.tool != tool:
                continue
            if not match_text(r.pattern, subject):
                continue
            if r.verdict is Verdict.DENY:
                deny = True
            elif r.verdict is Verdict.ASK:
                ask = True
            else:
                allow = True
        if deny:
            return Verdict.DENY
        if ask:
            return Verdict.ASK
        if allow:
            return Verdict.ALLOW
        return None

    async def check(self, tool: str, arguments: dict) -> Decision:
        # 1) 黑名单 — 硬层，仅 run 命令
        if tool == "run":
            cmd = arguments.get("command") if isinstance(arguments.get("command"), str) else None
            if cmd and blacklist_hit(cmd):
                return Decision(allow=False, reason=f"黑名单命中高危命令: {cmd[:80]}")

        subject = self._subject(tool, arguments)

        # 会话内已放行（本会话），直接允许
        if subject is not None and (tool, subject) in self._session_cache:
            return Decision(allow=True)

        # 2) 路径沙箱 — 硬层，文件类工具
        if subject is not None and SUBJECT_KEYS.get(tool) == "file_path":
            escape = check_escape(subject, self.workspace)
            if escape:
                return Decision(allow=False, reason=escape)

        # 3) 规则层
        verdict = self._rule_verdict(tool, subject)
        if verdict is None:
            # 4) 无命中 → 模式兜底
            verdict = fallback_for(self.mode)

        if verdict is Verdict.DENY:
            return Decision(allow=False, reason=f"规则拒绝({tool})")
        if verdict is Verdict.ALLOW:
            return Decision(allow=True)

        # accept-edits：write/edit 的 ask 自动应用（run 除外）→ auto 标记，不弹框
        if verdict is Verdict.ASK and self.accept_edits and tool in ("write", "edit"):
            return Decision(allow=True, auto=True)

        # 5) ask → 人在回路
        return await self._ask(tool, subject)

    async def _ask(self, tool: str, subject: str | None) -> Decision:
        if self.human_callback is None:
            return Decision(allow=False, reason="需要人工确认但未配置确认回调")
        try:
            choice = await self.human_callback(tool, subject or "")
        except Exception as e:
            logger.warning("人机回环回调异常: %s", e)
            return Decision(allow=False, reason=f"确认回调异常: {e}")

        if choice is HumanChoice.DENY:
            return Decision(allow=False, reason="用户拒绝")
        if subject is not None and choice is HumanChoice.ALLOW_SESSION:
            self._session_cache.add((tool, subject))
            logger.info("会话放行: %s(%s)", tool, subject)
        if choice is HumanChoice.ALLOW_FOREVER:
            self._persist_rule(Rule(tool=tool, pattern=subject or "*", verdict=Verdict.ALLOW))
            logger.info("永久放行: %s(%s)", tool, subject)
        return Decision(allow=True)

    def _persist_rule(self, rule: Rule) -> None:
        """把永久放行写进本地规则文件；无路径则仅追加到内存规则。"""
        self.rules.append(rule)
        if not self.local_rules_path:
            return
        path = Path(self.local_rules_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = []
        if path.exists():
            with open(path, encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
        else:
            raw = {}
        data = raw.get("rules", []) or []
        data.append(f"{rule.tool}({rule.pattern}): {rule.verdict.value}")
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump({"rules": data}, f, allow_unicode=True, sort_keys=False)