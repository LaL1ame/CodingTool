"""危险命令黑名单 — 硬层拦截，不可被规则/档位/人机放开。

只作用于 run 工具的 command 串。正则考虑 shell 内置分隔/转义，尽量拦截已知高危动作。
"""

import re

BLACKLIST_PATTERNS: list[str] = [
    # 递归强制删除 /（仅拦裸根目录；rm -rf /tmp 不误伤）
    r"\brm\b\s+-\S*[rRfF]\S*\s+/\s*$",
    r"\brm\b\s+-\S*[rRfF]\S*\s+~",
    # 关机/重启/断电
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bpoweroff\b",
    r"\binit\s+0\b",
    # 磁盘格式化 / 危险写设备
    r"\bmkfs\b",
    r"\bdd\b\s+[\S\s]*of=/dev/",
    # 编码/混淆命令执行（常见绕过式 shell）
    r"powershell\s+.*\bencodedcommand\b",
    r"pwsh\s+.*\bencodedcommand\b",
]

BLACKLIST_RE: list[re.Pattern] = [re.compile(p, re.IGNORECASE) for p in BLACKLIST_PATTERNS]


def hit(command: str) -> str | None:
    """命中黑名单返回命中的 pattern 描述，否则 None。"""
    for pat in BLACKLIST_RE:
        if pat.search(command):
            return pat.pattern
    return None