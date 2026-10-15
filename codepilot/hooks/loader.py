"""Hook 规则加载器：两级 YAML 加载 + 集中校验（fail-fast）。

文件缺失/空忽略；任何非法规则抛 HookError（消息含来源文件+序号），
让启动期明确报错，不等运行到触发点才暴露（N1）。
"""

import re
from pathlib import Path

import yaml

from codepilot.hooks.model import (
    ACTION_TYPES,
    EVENT_FIELDS,
    EVENTS,
    INTERCEPT_EVENT,
    CondItem,
    Condition,
    HookAction,
    HookError,
    HookRule,
)
from codepilot.permissions.rules import is_regex_pattern, regex_body

# 动作类型 → 必填字段
_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "command": ("cmd",),
    "prompt": ("text",),
    "http": ("url",),
    "subagent": (),
}


def load_hooks(project_path: Path, user_path: Path) -> list[HookRule]:
    """读两级规则文件并合并。用户级在前、项目级在后（叠加语义，声明顺序即执行顺序）。

    文件缺失/为空/hooks 为空列表 → 跳过；任一规则非法 → HookError。
    """
    rules: list[HookRule] = []
    for path in (user_path, project_path):
        rules.extend(_load_file(Path(path)))
    return rules


def _load_file(path: Path) -> list[HookRule]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        try:
            data = yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            raise HookError(f"Hook 配置 YAML 解析失败: {path}: {e}") from e
    raw = data.get("hooks", []) or []
    if not isinstance(raw, list):
        raise HookError(f"Hook 配置错误: {path}: hooks 必须是列表")
    return [_parse_rule(item, str(path), i) for i, item in enumerate(raw)]


def _fail(source: str, ordinal: int, msg: str) -> HookError:
    return HookError(f"Hook 规则非法: {source}#{ordinal}: {msg}")


def _parse_rule(item, source: str, ordinal: int) -> HookRule:
    if not isinstance(item, dict):
        raise _fail(source, ordinal, "规则必须是映射（event/if/action）")

    # —— event 必填且已知 ——
    event = item.get("event")
    if not event:
        raise _fail(source, ordinal, "缺少必填 event")
    if event not in EVENTS:
        raise _fail(source, ordinal, f"未知事件: {event!r}（可选: {', '.join(EVENTS)}）")

    # —— action 必填 ——
    raw_action = item.get("action")
    if not isinstance(raw_action, dict):
        raise _fail(source, ordinal, "缺少必填 action（映射，含 type）")
    action = _parse_action(raw_action, event, source, ordinal)

    # —— if 可省略；简写 {field: pattern} 等价 all 单条 ——
    condition = _parse_condition(item.get("if"), event, source, ordinal)

    return HookRule(event=event, condition=condition, action=action,
                    source=source, ordinal=ordinal)


def _parse_action(raw: dict, event: str, source: str, ordinal: int) -> HookAction:
    atype = raw.get("type")
    if atype not in ACTION_TYPES:
        raise _fail(source, ordinal,
                    f"未知动作类型: {atype!r}（可选: {', '.join(ACTION_TYPES)}）")

    known = {"type", "timeout", "async", "once"}
    fields = {k: v for k, v in raw.items() if k not in known}
    for req in _REQUIRED_FIELDS[atype]:
        if not fields.get(req):
            raise _fail(source, ordinal, f"{atype} 动作缺少必填字段 {req!r}")

    is_async = bool(raw.get("async", False))
    if is_async and event == INTERCEPT_EVENT:
        raise _fail(source, ordinal,
                    f"{INTERCEPT_EVENT} 是拦截类事件，禁止 async（拦截必须同步出结果）")

    timeout = raw.get("timeout")
    if timeout is not None and (not isinstance(timeout, (int, float)) or timeout <= 0):
        raise _fail(source, ordinal, f"timeout 必须是正数: {timeout!r}")

    return HookAction(type=atype, fields=fields,
                      timeout=float(timeout) if timeout is not None else None,
                      is_async=is_async, once=bool(raw.get("once", False)))


def _parse_condition(raw, event: str, source: str, ordinal: int) -> Condition | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise _fail(source, ordinal, "if 必须是映射（{field: pattern} 或 {all|any: [...]}）")

    has_all = "all" in raw
    has_any = "any" in raw
    if has_all and has_any:
        raise _fail(source, ordinal, "if.all 与 if.any 二选一，不混用")

    if has_all or has_any:
        mode = "all" if has_all else "any"
        entries = raw[mode]
        if not isinstance(entries, list) or not entries:
            raise _fail(source, ordinal, f"if.{mode} 必须是非空列表")
        items = tuple(_parse_cond_item(e, event, source, ordinal) for e in entries)
    else:
        # 简写：{field: pattern}（可多个键，等价 all）
        if not raw:
            raise _fail(source, ordinal, "if 为空映射；省略 if 表示无条件")
        items = tuple(
            _parse_cond_item({k: v}, event, source, ordinal) for k, v in raw.items()
        )
        mode = "all"
    return Condition(mode=mode, items=items)


def _parse_cond_item(entry, event: str, source: str, ordinal: int) -> CondItem:
    if not isinstance(entry, dict) or len(entry) != 1:
        raise _fail(source, ordinal, f"条件条目必须是单键映射 {{field: pattern}}: {entry!r}")
    field, pattern = next(iter(entry.items()))

    # 字段存在性校验：tool 事件的参数键放开（MCP 工具参数无法穷举），
    # 非 tool 事件必须在注册表内。
    if event not in ("tool_pre", "tool_post"):
        allowed = EVENT_FIELDS[event]
        if field not in allowed:
            raise _fail(
                source, ordinal,
                f"事件 {event} 没有条件字段 {field!r}"
                + (f"（可选: {', '.join(sorted(allowed))}）" if allowed else "（该事件无条件字段，省略 if）"))

    pattern = str(pattern)
    # 正则合法性加载期校验（剥离 ! 前缀后判定）
    body = pattern[1:] if pattern.startswith("!") else pattern
    if is_regex_pattern(body):
        try:
            re.compile(regex_body(body))
        except re.error as e:
            raise _fail(source, ordinal, f"正则非法: {pattern!r}: {e}") from e
    return CondItem(field=field, pattern=pattern)
