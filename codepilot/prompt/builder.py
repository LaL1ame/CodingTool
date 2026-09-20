"""System Prompt 拼装器：稳定前缀 + 动态尾块 → 协议中立的 SystemPrompt。

限职责：组装。序列化交给协议层，按轮注入交给 agent 循环。
"""

from dataclasses import dataclass

from codepilot.prompt.sections import (
    FIXED_SECTIONS,
    PLAN_CONCISE_TEXT,
    PLAN_FULL_TEXT,
    SESSION_TAG,
    Section,
    format_env,
)


@dataclass
class EnvContext:
    """运行时可注入的环境信息。"""
    cwd: str
    date: str


@dataclass
class SystemPrompt:
    """协议中立的分层系统提示。序列化规则由各协议决定。"""
    stable: list[str]    # 稳定前缀块（缓存区，断点在末位）
    volatile: list[str]  # 动态尾块（断点后，不进缓存）


def _prompt_tag(text: str) -> str:
    """把一段指令包进特殊标签，标明它是系统级会话指令。"""
    return f"<{SESSION_TAG}>\n{text}\n</{SESSION_TAG}>"


def _plan_blocks(
    plan_mode: bool, round_num: int, refresh_every: int
) -> list[str]:
    """plan 指令按轮注入：首轮全文 / 每 N 轮精简 / 其余跳过。"""
    if not plan_mode:
        return []
    if round_num == 1:
        return [_prompt_tag(PLAN_FULL_TEXT)]
    if refresh_every > 0 and round_num % refresh_every == 0:
        return [_prompt_tag(PLAN_CONCISE_TEXT)]
    return []


def build_system_prompt(
    env: EnvContext,
    *,
    plan_mode: bool,
    round_num: int,
    refresh_every: int,
) -> SystemPrompt:
    """生成当前轮的分层系统提示。"""
    stable = [s.content for s in sorted(FIXED_SECTIONS, key=lambda s: s.priority)]
    volatile = [format_env(env.cwd, env.date)]
    volatile.extend(_plan_blocks(plan_mode, round_num, refresh_every))
    return SystemPrompt(stable=stable, volatile=volatile)