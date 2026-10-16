"""Hook 动作执行器：command / http / prompt / subagent（占位）。

各自故障隔离：异常不外抛，以返回值或日志表达失败（N2）。
占位符渲染缺字段保留原样不炸。
"""

import asyncio
import json
import locale
import logging
import os
import subprocess
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


def _kill_tree(proc: asyncio.subprocess.Process) -> None:
    """杀掉整个进程树，避免超时/取消后孤儿进程握着管道句柄不放。

    Windows: shell(cmd)自身被杀不足以终结孙进程，用 taskkill /T 连树杀；
    其它平台直接 kill。
    """
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True, timeout=3)
        else:
            proc.kill()
    except Exception as e:
        logger.debug("hook command 进程树清理失败: %s", e)


async def _bounded_wait(proc: asyncio.subprocess.Process) -> None:
    """杀树后有界等待回收；仍不退出就放弃（记日志，不阻塞调用方）。"""
    try:
        await asyncio.wait_for(proc.wait(), timeout=2.0)
    except asyncio.TimeoutError:
        logger.debug("hook command 进程树未能完全回收，放任残余进程退出")


async def run_command(cmd: str, fields: dict, timeout: float,
                      event: str = "", tool: str = "") -> CommandOutcome:
    """执行 shell 命令。事件上下文经环境变量传给子进程。"""
    env_extra = {
        "CODEPILOT_HOOK_EVENT": event,
        "CODEPILOT_TOOL": tool,
        "CODEPILOT_TOOL_ARGS": json.dumps(fields, ensure_ascii=False, default=str),
    }
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
        _kill_tree(proc)
        await _bounded_wait(proc)
        return CommandOutcome(exit_code=-1, stdout="", stderr="", timed_out=True)
    except asyncio.CancelledError:
        _kill_tree(proc)
        await _bounded_wait(proc)
        raise
    return CommandOutcome(
        exit_code=proc.returncode or 0,
        stdout=_decode(out),
        stderr=_decode(err),
    )


def _decode(raw: bytes) -> str:
    """子进程输出解码：先 UTF-8，失败回落系统区域编码（中文 Windows GBK）。"""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode(locale.getpreferredencoding(False), errors="replace")


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
