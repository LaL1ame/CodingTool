"""CodePilot 入口。"""

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

from codepilot.chat import Message
from codepilot.config import (
    ConfigError, load_agent_config, load_config, load_log_config,
    load_permissions_config,
)
from codepilot.logging_setup import setup_logging
from codepilot.permissions import PermissionManager
from codepilot.permissions.loader import load_rules_from_files
from codepilot.permissions.mode import PermissionMode
from codepilot.prompt import EnvContext
from codepilot.providers import BaseProvider
from codepilot.agent import Agent
from codepilot.tools import ToolRegistry, ReadTool, WriteTool, EditTool, GlobTool, GrepTool, RunTool
from codepilot.ui.app import CodePilotApp


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


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="codepilot", description="CodePilot CLI")
    parser.add_argument("-c", "--continue", dest="continue_", action="store_true",
                        help="继续最近一次会话")
    parser.add_argument("-r", "--resume", action="store_true",
                        help="列出历史会话并选择一条恢复")
    return parser.parse_args()


def _pick_session(cwd: Path, continue_: bool, resume: bool) -> tuple[Path, dict]:
    """选择要恢复的会话文件,返回 (path, loaded)。无历史则新建。"""
    from codepilot.session import store
    sessions = store.list_sessions(cwd)
    if not (continue_ or resume) or not sessions:
        return store.new_session_path(cwd), {}
    if resume and len(sessions) > 1:
        print("历史会话:")
        for i, p in enumerate(sessions[:10]):
            t = datetime.fromtimestamp(p.stat().st_mtime).strftime("%m-%d %H:%M")
            print(f"  [{i+1}] {t}  {p.name}")
        try:
            idx = int(input("选择编号: ").strip()) - 1
            if 0 <= idx < len(sessions):
                path = sessions[idx]
            else:
                path = sessions[0]
        except (ValueError, EOFError, KeyboardInterrupt):
            path = sessions[0]
    else:
        path = sessions[0]  # -c 或单条:续最新
    return path, store.load_session(path)


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

    # —— 权限系统：读三级规则文件 + 模式，构造 PermissionManager ——
    perms_cfg = load_permissions_config(path)
    mode = getattr(PermissionMode, perms_cfg.mode.upper(), PermissionMode.DEFAULT)
    # 同步给正交模式状态机:permissive→auto / default→manual / strict→strict
    startup_release = {"PERMISSIVE": "auto", "DEFAULT": "manual",
                       "STRICT": "strict"}.get(mode.name, "manual")
    user_rules = Path(perms_cfg.user_rules) if perms_cfg.user_rules else \
        Path.home() / ".config" / "codepilot" / "permissions.yaml"
    project_rules = Path(perms_cfg.project_rules) if perms_cfg.project_rules else \
        workspace / "codepilot-permissions.yaml"
    local_rules = Path(perms_cfg.local_rules) if perms_cfg.local_rules else \
        workspace / "codepilot-permissions.local.yaml"
    permission = PermissionManager(
        workspace=workspace, mode=mode,
        rules=load_rules_from_files([user_rules, project_rules]),
        local_rules_path=local_rules,
    )

    env = EnvContext(cwd=str(workspace), date=datetime.now().strftime("%Y-%m-%d"))
    agent = Agent(provider=provider, registry=registry, workspace=workspace,
                  max_rounds=agent_config.max_rounds, max_unknown=agent_config.max_unknown,
                  env=env, refresh_every=agent_config.prompt_refresh_every,
                  permission=permission)

    # —— 会话 resume：-c 续最近 / -r 列表选择,恢复消息 + 模式快照 ——
    args = _parse_args()
    session_path, loaded = _pick_session(workspace, args.continue_, args.resume)
    history = [Message(role=m["role"], content=m["content"],
                       duration=m.get("duration")) for m in loaded.get("messages", [])
               if m.get("role") in ("user", "assistant")]

    from codepilot.session import store
    store.append_meta(session_path, provider=cfg.name, model=cfg.model, cwd=str(workspace))
    store.project_index_dir(workspace).mkdir(exist_ok=True)

    app = CodePilotApp(agent, release=startup_release, history=history,
                       session_file=str(session_path), initial_modes=loaded.get("mode"))
    # 权限人机回路：App 的 ask_permission 作为 PermissionManager 的 human_callback
    permission.human_callback = app.ask_permission

    app.run()


if __name__ == "__main__":
    main()
