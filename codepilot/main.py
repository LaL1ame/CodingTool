"""CodePilot 入口。"""

import os
import sys
from datetime import datetime
from pathlib import Path

from codepilot.config import ConfigError, load_agent_config, load_config, load_log_config
from codepilot.logging_setup import setup_logging
from codepilot.prompt import EnvContext
from codepilot.providers import BaseProvider
from codepilot.agent import Agent
from codepilot.tools import ToolRegistry, ReadTool, WriteTool, EditTool, GlobTool, GrepTool, RunTool
from codepilot.app import CodePilotApp


def find_config() -> Path:
    cwd = Path.cwd() / "config.yaml"
    if cwd.exists():
        return cwd
    home = Path.home() / ".config" / "codepilot" / "config.yaml"
    if home.exists():
        return home
    return cwd


def _build_registry(workspace: Path) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ReadTool(workspace))
    registry.register(WriteTool(workspace))
    registry.register(EditTool(workspace))
    registry.register(GlobTool(workspace))
    registry.register(GrepTool(workspace))
    registry.register(RunTool(workspace))
    return registry


def main() -> None:
    path = os.environ.get("MEWCODE_CONFIG", str(find_config()))

    # 日志落盘：优先环境变量覆盖，其次 config.yaml 的 log: 段
    log_cfg = load_log_config(path)
    if os.environ.get("MEWCODE_LOG"):
        log_cfg.file = os.environ["MEWCODE_LOG"]
    setup_logging(log_cfg.file, log_cfg.level)

    try:
        configs = load_config(path)
    except ConfigError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)
    if not configs:
        print("No providers configured.", file=sys.stderr)
        sys.exit(1)

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

    workspace = Path.cwd()
    agent_config = load_agent_config(path)
    provider = BaseProvider.create(cfg)
    registry = _build_registry(workspace)
    env = EnvContext(cwd=str(workspace), date=datetime.now().strftime("%Y-%m-%d"))
    agent = Agent(provider=provider, registry=registry, workspace=workspace,
                  max_rounds=agent_config.max_rounds, max_unknown=agent_config.max_unknown,
                  env=env, refresh_every=agent_config.prompt_refresh_every)

    app = CodePilotApp(agent)
    # 注入确认回调到 RunTool
    run_tool = registry.get("run")
    if run_tool is not None:
        run_tool.confirm_callback = app.confirm_command  # type: ignore[attr-defined]

    app.run()


if __name__ == "__main__":
    main()
