"""ASCII 猫咪横幅 —— 启动时展示。"""

import os

from textual.widgets import Static

CAT_ASCII = r"""
  ╱|、
 (˚ˎ 。7
  |、˜〵
  じしˍ,)ノ
"""

VERSION = "0.1.0"


def build_banner_text() -> str:
    cwd = os.getcwd()
    # 截断过长的路径
    if len(cwd) > 60:
        cwd = "..." + cwd[-57:]
    return (
        f"[bold cyan]{CAT_ASCII}[/bold cyan]\n"
        f"[bold]MewCode[/bold] v{VERSION}\n"
        f"[dim]{cwd}[/dim]"
    )


class BannerWidget(Static):
    """启动横幅：ASCII 猫咪 + 应用名/版本 + 工作目录。"""

    def on_mount(self) -> None:
        self.update(build_banner_text())
