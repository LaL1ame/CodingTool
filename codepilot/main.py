"""CodePilot 入口。"""

import argparse
import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

from codepilot.chat import Message
from codepilot.config import (
    ConfigError, load_agent_config, load_config, load_context_config,
    load_log_config, load_mcp_config, load_memory_config,
    load_permissions_config, load_session_config,
)
from codepilot.context import ContextManager
from codepilot.logging_setup import setup_logging
from codepilot.permissions import PermissionManager
from codepilot.permissions.loader import load_rules_from_files
from codepilot.permissions.mode import PermissionMode
from codepilot.prompt import EnvContext
from codepilot.providers import BaseProvider
from codepilot.agent import Agent
from codepilot.mcp import McpManager
from codepilot.tools import ToolRegistry, ReadTool, WriteTool, EditTool, GlobTool, GrepTool, RunTool
from codepilot import skills as _skills
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
    parser.add_argument("--new", action="store_true",
                        help="强制新开会话,不复原(覆盖 auto_resume)")
    return parser.parse_args()


def _pick_session(cwd: Path, continue_: bool, resume: bool, new: bool,
                  auto_resume: bool) -> tuple[Path, dict | None]:
    """选择要恢复的会话文件,返回 (path, loaded|None)。

    loaded=None 表示新开不复原。启动即执行过期清理(state12)。
    默认(auto_resume=False)=新开会话;仅 -c/--resume 或 config 开 auto_resume 才接最近。
    """
    from codepilot.session import store
    import logging
    log = logging.getLogger("codepilot")
    n_cleaned = len(store.cleanup_expired(store.session_dir(cwd)))
    if n_cleaned:
        log.info("过期清理: 删除 %d 个会话文件", n_cleaned)
    sessions = store.list_sessions(cwd)
    if new or not sessions or not (continue_ or resume or auto_resume):
        log.info("新开会话(无续接)")
        return store.new_session_path(cwd), None
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
        path = sessions[0]  # -c / auto_resume / 单条:续最新
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

    # —— 上下文管理(state9):context: 段配置 + ContextManager(chat 由 App.on_mount 补绑)——
    ctx_config = load_context_config(path)
    context_manager = ContextManager(
        provider=provider, config=ctx_config,
        offload_dir=workspace / ".codepilot" / "context",
    )
    # —— 长期记忆(state13):指令文件 + 自动笔记 + 注入 ——
    memory_cfg = load_memory_config(path)
    from codepilot.memory import MemoryInjector, NoteStore, auto
    memory = MemoryInjector(workspace, store=NoteStore(workspace))
    auto_memory_cb = None
    if memory_cfg.auto_memory:
        def _auto_memory(msgs: list[dict]) -> None:
            auto.schedule(provider, memory.store, msgs)
        auto_memory_cb = _auto_memory

    # —— Skill 系统(state15):三级加载 + 系统级 load_skill 工具 + 两阶段注入 ——
    skill_manager = _skills.SkillManager(
        _skills.load_all(workspace,
                         user_root=Path.home() / ".skills",
                         builtin_root=_skills.BUILTIN_DIR,
                         check_tools=lambda n: registry.get(n) is not None),
    )
    registry.register(_skills.LoadSkillTool(skill_manager))  # 系统工具,不受白名单约束

    agent = Agent(provider=provider, registry=registry, workspace=workspace,
                  max_rounds=agent_config.max_rounds, max_unknown=agent_config.max_unknown,
                  env=env, refresh_every=agent_config.prompt_refresh_every,
                  permission=permission, context_manager=context_manager,
                  memory_injector=memory.build,
                  skill_injector=skill_manager.skill_blocks,
                  skill_whitelist=skill_manager.active_whitelist)

    # —— 会话 resume(state12)：-c 续最近 / -r 列表选择 / --new 强制新开;
    #    默认按 session.auto_resume 自动接最近。恢复走 restore_chat(截断/提醒),
    #    完整 LLM 序列作 seed_msgs 回灌 ChatManager,工具上下文跨重启保留 ——
    args = _parse_args()
    session_cfg = load_session_config(path)
    session_path, loaded = _pick_session(workspace, args.continue_, args.resume,
                                         args.new, session_cfg.auto_resume)
    restored = {"messages": [], "mode": None}
    if loaded is not None:
        from codepilot.session import restore
        restored = restore.restore_chat(session_path, gap_hours=session_cfg.gap_hours)
    history = [Message(role=m.get("role"), content=str(m.get("content") or ""),
                       duration=None) for m in restored.get("messages", [])
               if m.get("role") in ("user", "assistant")]

    from codepilot.session import store
    store.append_meta(session_path, provider=cfg.name, model=cfg.model, cwd=str(workspace))
    store.project_index_dir(workspace).mkdir(exist_ok=True)

    app = CodePilotApp(agent, release=startup_release, history=history,
                       session_file=str(session_path),
                       initial_modes=loaded.get("mode") if loaded else None,
                       context_manager=context_manager,
                       seed_msgs=restored.get("messages"),
                       auto_memory=auto_memory_cb,
                       memory_injector=memory,
                       skill_manager=skill_manager)

    # —— MCP:启动时连接并注册工具。必须与 App 同一事件循环——
    # app.run() 内部会自开一个 loop;若先用另一段 asyncio.run 连接 stdio/http
    # 传输,那些通道会绑死在已关闭的 loop 上,进 App 后全部失效。故统一在此 loop。
    user_cfg = Path.home() / ".config" / "codepilot" / "config.yaml"
    proj_cfg = Path(os.environ.get("MEWCODE_CONFIG", str(Path.cwd() / "config.yaml")))
    mcp_specs = load_mcp_config(user_cfg, proj_cfg)
    mcp_manager = McpManager(registry)

    async def _run_ui() -> None:
        # MCP 后台连接:不阻塞 TUI 启动(远端 server 握手可能数秒/超时)。
        # Agent 每轮现读 registry,工具就绪后自动出现;启动即用的取舍:
        # "Agent 消费前注册"由同步保证放宽为"就绪即用"(后台连上即注册)。
        connect_task: asyncio.Task | None = None
        if mcp_specs:
            connect_task = asyncio.create_task(mcp_manager.connect_all(mcp_specs))
        # 权限人机回路：App 的 ask_permission 作为 PermissionManager 的 human_callback
        permission.human_callback = app.ask_permission
        await app.run_async()
        if connect_task is not None and not connect_task.done():
            connect_task.cancel()
            try:
                await connect_task
            except (asyncio.CancelledError, Exception):
                pass
        await mcp_manager.close_all()

    asyncio.run(_run_ui())


if __name__ == "__main__":
    main()
