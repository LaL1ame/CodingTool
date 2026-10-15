"""Hook 动作执行器：command / http / prompt / subagent（占位）。

各自故障隔离：异常不外抛，以返回值或日志表达失败（N2）。
占位符渲染缺字段保留原样不炸。
"""

import asyncio
import json
import logging
import urllib.request
from dataclasses import dataclass
from string import Formatter

from codepilot.hooks.injector import HookInjector

logger = logging.getLogger("codepilot.hooks")


class _KeepMissing(dict):
    """format_map 容错：缺字段保留 {name} 原样。"""

    def __missing__(self, key):
        return "{" + key + "}"


def render(text: str, fields: dict) -> str:
    """渲染 {field} 占位符。格式串本身非法（如不配对括号）时返回原文。"""
    try:
        # 先校验格式串可解析，避免 format_map 半路抛 ValueError
        list(Formatter().parse(text))
        return text.format_map(_KeepMissing(fields))
    except (ValueError, KeyError):
        return text


@dataclass
class CommandOutcome:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    error: str = ""            # 启动失败等异常（非进程退出码）


async def run_command(cmd: str, fields: dict, timeout: float,
                      event: str = "", tool: str = "") -> CommandOutcome:
    """执行 shell 命令。事件上下文经环境变量传给子进程。"""
    env_extra = {
        "CODEPILOT_HOOK_EVENT": event,
        "CODEPILOT_TOOL": tool,
        "CODEPILOT_TOOL_ARGS": json.dumps(fields, ensure_ascii=False, default=str),
    }
    import os
    env = {**os.environ, **env_extra}
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except Exception as e:  # 启动失败（解释器不存在等）
        return CommandOutcome(exit_code=-1, stdout="", stderr="", error=str(e))
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        try:
            # Windows: shell(cmd)被杀但孙进程可能继承管道句柄存活，
            # wait() 会等到管道关闭 → 有界等待，孤儿进程任其自行退出。
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            logger.debug("hook command 超时后进程树未能完全收尾，放任孤儿退出")
        return CommandOutcome(exit_code=-1, stdout="", stderr="", timed_out=True)
    return CommandOutcome(
        exit_code=proc.returncode or 0,
        stdout=out.decode("utf-8", errors="replace"),
        stderr=err.decode("utf-8", errors="replace"),
    )


async def run_http(url: str, method: str = "POST",
                   headers: dict | None = None, body: str | None = None,
                   timeout: float = 30.0) -> tuple[bool, str]:
    """发 HTTP 请求（通知型 webhook）。响应只记日志；异常返回 (False, 原因)。"""

    def _send() -> tuple[bool, str]:
        data = body.encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            url, data=data, method=method.upper(),
            headers={"Content-Type": "text/plain; charset=utf-8", **(headers or {})},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, f"HTTP {resp.status}"

    try:
        ok, detail = await asyncio.to_thread(_send)
        logger.info("hook http %s %s -> %s", method, url, detail)
        return ok, detail
    except Exception as e:
        logger.warning("hook http %s %s failed: %s", method, url, e)
        return False, str(e)


def run_prompt(text: str, fields: dict, injector: HookInjector, key: str) -> None:
    """注入提示词：渲染占位符后钉进 volatile 块仓库（同 key 覆盖去重）。"""
    injector.add(key, render(text, fields))


def run_subagent() -> None:
    """subagent 占位：本阶段不接真实执行（等 SubAgent 章节对接）。"""
    logger.info("hook subagent 动作未实现（占位），跳过")
