"""HookEngine —— Hook 核心引擎：匹配 + 触发 + 执行控制 + 拦截通道。

零规则短路（N5）；任何动作失败只记日志 + notify，绝不外抛（N2/F9）。
不 import agent/ui/textual——对 UI 的触达只有 notify 回调（N3）。
"""

import asyncio
import logging
from collections.abc import Callable

from codepilot.hooks.actions import (
    render,
    run_command,
    run_http,
    run_prompt,
    run_subagent,
)
from codepilot.hooks.injector import HookInjector
from codepilot.hooks.model import HookRule
from codepilot.permissions.rules import match_text

logger = logging.getLogger("codepilot.hooks")

DEFAULT_COMMAND_TIMEOUT = 60.0
DEFAULT_HTTP_TIMEOUT = 30.0
_ACLOSE_BUDGET = 5.0
_DENY_REASON_MAX = 500


def _str_fields(fields: dict) -> dict:
    """入口统一 str() 化：bool→true/false，其余 str()。匹配永远是字符串语义。"""
    out = {}
    for k, v in fields.items():
        if isinstance(v, bool):
            out[k] = "true" if v else "false"
        else:
            out[k] = v if isinstance(v, str) else str(v)
    return out


class HookEngine:
    def __init__(self, rules: list[HookRule], injector: HookInjector,
                 notify: Callable[[str], None] | None = None) -> None:
        self._rules = rules
        self._injector = injector
        self.notify = notify
        self._fired_once: set[tuple[str, int]] = set()
        self._bg_tasks: set[asyncio.Task] = set()

    # —— 匹配 ——

    def _matched(self, event: str, fields: dict) -> list[HookRule]:
        """命中规则（声明顺序）。fields 须已 str 化。"""
        hit = []
        for r in self._rules:
            if r.event != event:
                continue
            if r.condition is None:
                hit.append(r)
                continue
            results = [
                match_text(c.pattern, fields.get(c.field))
                for c in r.condition.items
            ]
            ok = all(results) if r.condition.mode == "all" else any(results)
            if ok:
                hit.append(r)
        return hit

    # —— 触发 ——

    async def fire(self, event: str, fields: dict) -> None:
        """非拦截事件入口。命中按声明顺序执行；失败记日志+notify，不外抛。"""
        if not self._rules:
            return
        sfields = _str_fields(fields)
        for rule in self._matched(event, sfields):
            await self._trigger(rule, event, sfields)

    async def _trigger(self, rule: HookRule, event: str, fields: dict) -> None:
        key = (rule.source, rule.ordinal)
        if rule.action.once:
            if key in self._fired_once:
                logger.debug("hook %s#%d once 已触发，跳过", *key)
                return
            self._fired_once.add(key)
        if rule.action.is_async:
            task = asyncio.create_task(
                self._execute_guarded(rule, event, fields))
            self._bg_tasks.add(task)
            task.add_done_callback(self._on_bg_done(rule))
            return
        await self._execute_guarded(rule, event, fields)

    def _on_bg_done(self, rule: HookRule):
        def _cb(task: asyncio.Task) -> None:
            self._bg_tasks.discard(task)
            tag = f"{rule.source}#{rule.ordinal}"
            if task.cancelled():
                logger.info("hook %s 后台任务被取消", tag)
                self._notify(f"[hook] {tag} cancelled")
                return
            outcome = task.result()
            if outcome:
                logger.info("hook %s 后台任务完成", tag)
                self._notify(f"[hook] {tag} ok")
            else:
                logger.warning("hook %s 后台任务失败", tag)
                self._notify(f"[hook] {tag} failed")
        return _cb

    def _notify(self, msg: str) -> None:
        if self.notify is not None:
            try:
                self.notify(msg)
            except Exception as e:
                logger.warning("hook notify 回调异常: %s", e)

    async def _execute_guarded(self, rule: HookRule, event: str,
                               fields: dict) -> bool:
        """执行单条规则动作。返回是否成功；任何异常吞掉记日志（N2）。"""
        tag = f"{rule.source}#{rule.ordinal}"
        try:
            await self._execute(rule, event, fields)
            logger.info("hook %s (%s) ok", tag, event)
            return True
        except Exception as e:
            logger.warning("hook %s (%s) failed: %s", tag, event, e)
            self._notify(f"[hook] {tag} failed: {e}")
            return False

    async def _execute(self, rule: HookRule, event: str, fields: dict) -> None:
        a = rule.action
        if a.type == "command":
            cmd = render(str(a.fields["cmd"]), fields)
            outcome = await run_command(
                cmd, fields,
                timeout=a.timeout or DEFAULT_COMMAND_TIMEOUT,
                event=event, tool=fields.get("tool", ""))
            if outcome.timed_out:
                raise RuntimeError(f"命令超时 ({a.timeout or DEFAULT_COMMAND_TIMEOUT:.0f}s)")
            if outcome.error:
                raise RuntimeError(f"命令启动失败: {outcome.error}")
            if outcome.exit_code != 0:
                raise RuntimeError(
                    f"命令退出码 {outcome.exit_code}: {outcome.stderr[:200]}")
        elif a.type == "prompt":
            run_prompt(str(a.fields["text"]), fields, self._injector,
                       key=f"{rule.source}#{rule.ordinal}")
        elif a.type == "http":
            ok, detail = await run_http(
                url=render(str(a.fields["url"]), fields),
                method=str(a.fields.get("method", "POST")),
                headers=a.fields.get("headers"),
                body=render(str(a.fields["body"]), fields)
                if a.fields.get("body") is not None else None,
                timeout=a.timeout or DEFAULT_HTTP_TIMEOUT,
            )
            if not ok:
                raise RuntimeError(f"HTTP 失败: {detail}")
        elif a.type == "subagent":
            run_subagent()

    # —— tool_pre 拦截通道 ——

    async def fire_pre_tool(self, tool: str, arguments: dict) -> str | None:
        """tool_pre 专用可拦截通道。

        command 动作同步逐个执行：首个非零退出 → 返回拒绝原因（stderr 截断）；
        异常/超时 → 记日志放行（fail-open，决策B）。其他动作照常执行不影响判定。
        """
        if not self._rules:
            return None
        fields = _str_fields({"tool": tool, **arguments})
        for rule in self._matched("tool_pre", fields):
            if rule.action.type == "command":
                reason = await self._intercept_check(rule, fields)
                if reason is not None:
                    return reason
            else:
                await self._trigger(rule, "tool_pre", fields)
        return None

    async def _intercept_check(self, rule: HookRule, fields: dict) -> str | None:
        tag = f"{rule.source}#{rule.ordinal}"
        a = rule.action
        cmd = render(str(a.fields["cmd"]), fields)
        timeout = a.timeout or DEFAULT_COMMAND_TIMEOUT
        outcome = await run_command(cmd, fields, timeout=timeout,
                                    event="tool_pre",
                                    tool=fields.get("tool", ""))
        if outcome.timed_out:
            logger.warning("hook %s 拦截命令超时(%.0fs)，放行", tag, timeout)
            self._notify(f"[hook] {tag} timeout, allowed")
            return None
        if outcome.error:
            logger.warning("hook %s 拦截命令启动失败: %s，放行", tag, outcome.error)
            self._notify(f"[hook] {tag} error, allowed")
            return None
        if outcome.exit_code != 0:
            reason = outcome.stderr.strip()[:_DENY_REASON_MAX] or \
                f"hook 拦截（退出码 {outcome.exit_code}）"
            logger.info("hook %s 拦截: %s", tag, reason)
            self._notify(f"[hook] {tag} denied: {reason[:80]}")
            return reason
        return None

    # —— 收尾 ——

    async def aclose(self) -> None:
        """退出时：有界等待后台任务，未完成的取消。"""
        if not self._bg_tasks:
            return
        done, pending = await asyncio.wait(self._bg_tasks, timeout=_ACLOSE_BUDGET)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._bg_tasks.clear()
