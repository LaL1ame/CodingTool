"""MewCode 入口。"""

import os
import sys
from pathlib import Path

from mewcode.config import ConfigError, load_config
from mewcode.app import MewCodeApp


def find_config() -> Path:
    cwd = Path.cwd() / "config.yaml"
    if cwd.exists():
        return cwd
    home = Path.home() / ".config" / "mewcode" / "config.yaml"
    if home.exists():
        return home
    return cwd


def main() -> None:
    path = os.environ.get("MEWCODE_CONFIG", str(find_config()))
    try:
        configs = load_config(path)
    except ConfigError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)
    if not configs:
        print("No providers configured.", file=sys.stderr)
        sys.exit(1)

    # 单 provider 直接进对话；多 provider 简单选择
    if len(configs) == 1:
        cfg = configs[0]
    else:
        print("Multiple providers found:")
        for i, c in enumerate(configs):
            print(f"  [{i+1}] {c.name}  ({c.protocol})  {c.model}")
        while True:
            try:
                choice = input("Select (number): ").strip()
                idx = int(choice) - 1
                if 0 <= idx < len(configs):
                    cfg = configs[idx]
                    break
            except (ValueError, EOFError, KeyboardInterrupt):
                print("Cancelled.")
                sys.exit(1)
            print(f"Enter 1-{len(configs)}")

    app = MewCodeApp(cfg)
    app.run()


if __name__ == "__main__":
    main()
