"""命令解析 — 识别 /exit /plan /do 及带消息的元命令。

从 TUI 层抽出，纯函数、不依赖 textual，便于单元测试。
"""


def parse_command(text: str) -> tuple[str | None, str]:
    """解析输入，返回 (命令, 剩余文本)。

    命令以斜杠开头、按首个空白分隔：
      /exit          → ("exit", "")
      /plan          → ("plan", "")
      /plan <消息>   → ("plan", "<消息>")   # 切计划模式并发送 <消息>
      /do            → ("do", "")
      /do <消息>     → ("do", "<消息>")
    非命令（普通消息）→ (None, 原文)
    """
    first, _, rest = text.partition(" ")
    if first == "/exit":
        return ("exit", "")
    if first == "/plan":
        return ("plan", rest.strip())
    if first == "/do":
        return ("do", rest.strip())
    return (None, text)
