"""MewCode 入口 —— 加载配置 → 启动 TUI。"""

import os
import sys
from pathlib import Path

from mewcode.config import ConfigError, load_config
from mewcode.tui.app import MewCodeApp

DEFAULT_CONFIG_NAME = "config.yaml"


def find_config() -> Path:
    """查找配置文件：当前目录 > 用户目录。"""
    cwd = Path.cwd() / DEFAULT_CONFIG_NAME
    if cwd.exists():
        return cwd

    home = Path.home() / ".config" / "mewcode" / DEFAULT_CONFIG_NAME
    if home.exists():
        return home

    # 回退到当前目录（加载时会报清晰的错误）
    return cwd


def main() -> None:
    """MewCode CLI 入口。"""
    config_path = os.environ.get("MEWCODE_CONFIG", str(find_config()))

    try:
        configs = load_config(config_path)
    except ConfigError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)

    if not configs:
        print("No providers configured.", file=sys.stderr)
        sys.exit(1)

    app = MewCodeApp(configs)
    app.run()


if __name__ == "__main__":
    main()
