"""路径沙箱 — 限制文件操作目标落在工作目录内，防 `..` / 符号链接逃逸。

先 resolve() 解析真实路径（解开符号链接），再判是否仍为工作目录子树。
"""

from pathlib import Path


def check_escape(file_path: str, workspace: Path) -> str | None:
    """file_path 若逃逸出 workspace 返回原因，否则返回 None。

    Arguments:
        file_path: 相对或绝对路径串。
        workspace: 许可的项目根目录。

    Returns:
        越界说明字符串；未逃逸时为 None。
    """
    ws = workspace.resolve()
    try:
        candidate = (ws / file_path).resolve()
    except (OSError, ValueError):
        return f"路径解析失败: {file_path}"
    if not candidate.is_relative_to(ws):
        return (
            f"路径越界: {file_path!r} 解析后落在工作目录之外 "
            f"({candidate})"
        )
    return None