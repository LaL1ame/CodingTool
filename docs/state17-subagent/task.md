# state17 SubAgent 系统 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `codepilot/subagents/model.py` | AgentRole / SubAgentTask / RoleError / 字段常量 |
| 修改 | `codepilot/agent.py` | `tool_filter` 可选参数 + `_build_tools` 末尾调用 |
| 新建 | `codepilot/subagents/loader.py` | 四级发现 + frontmatter 解析 + 覆盖 + 集中校验 |
| 新建 | `codepilot/subagents/runner.py` | `build_tool_filter` 防线纯函数 + `run_role_agent` / `run_fork_agent` |
| 新建 | `codepilot/subagents/manager.py` | SubAgentManager：委派/后台/深度/并发 |
| 新建 | `codepilot/subagents/tool.py` | AgentTool（常驻系统级工具） |
| 新建 | `codepilot/subagents/__init__.py` | 导出 SubAgentManager / AgentRole / AgentTool / RoleError |
| 新建 | `codepilot/subagents/data/architect.md` | 内置示例角色（放行 agent 样板） |
| 修改 | `codepilot/commands/builtins.py` | `/agents` `/bg` 命令（经 getattr 访问 manager） |
| 修改 | `codepilot/ui/app.py` | `_CommandContextImpl` 挂 subagent_manager + on_task_done 回灌接线 |
| 修改 | `codepilot/main.py` | 装配 SubAgentManager + 注册 AgentTool + 接线 on_task_done |
| 修改 | `pyproject.toml` | package-data 加 `subagents/data/*.md` |
| 新建 | `tests/subagents/test_model.py` | 数据模型 frozen/字段 |
| 新建 | `tests/subagents/test_loader.py` | 四级覆盖 + 校验全集 + 坏文件隔离 |
| 新建 | `tests/subagents/test_runner.py` | 定义式/Fork 构造独立 Agent（mock provider） |
| 新建 | `tests/subagents/test_filters.py` | 多层防线纯函数（白/黑名单、agent禁、后台只读、plan 继承） |
| 新建 | `tests/subagents/test_manager.py` | 并发/深度上限、转后台、取消、状态追踪 |
| 新建 | `tests/subagents/test_tool.py` | agent 工具参数分流 / 常驻 / 永不抛错 |

## T1: subagents/model.py 数据模型

**文件：** `codepilot/subagents/model.py`、`tests/subagents/test_model.py`
**依赖：** 无
**步骤：**
1. 定义 `RoleError(Exception)`。
2. 定义 `@dataclass(frozen=True) AgentRole`：`name/description/body/tools(frozenset)/blocked(frozenset)/model/max_rounds/permission/can_delegate(bool=False)/path/ordinal`。
3. 定义 `@dataclass SubAgentTask`：`task_id/kind/role/status/prompt/summary/usage/error/created_at`（可变，非 frozen）。
4. `import` 只依赖 stdlib（dataclasses, pathlib, typing）。
5. 测试：构造 AgentRole 断言 frozen（frozenset 字段、默认值）、SubAgentTask 字段可写。

**验证：** `python -m pytest tests/subagents/test_model.py -q` 全绿；`python -c "from codepilot.subagents.model import AgentRole, SubAgentTask, RoleError"` 导入成功。

## T2: agent.py 新增 tool_filter 注入点

**文件：** `codepilot/agent.py`、`tests/test_agent_tool_filter.py`
**依赖：** T1（可并行）
**步骤：**
1. `Agent.__init__` 新增参数 `tool_filter: Callable[[list[Tool]], list[Tool]] | None = None`，存 `self._tool_filter`。
2. `_build_tools` 末尾：`skill_whitelist` 与 `plan_mode` 过滤之后、`if not all_tools` 之前，插入 `if self._tool_filter is not None: all_tools = self._tool_filter(all_tools)`。
3. 测试：注入一个过滤函数（如只留 read），断言 `_build_tools` 返回被过滤；不注入时行为与现状一致（回归）。

**验证：** `python -m pytest tests/test_agent_tool_filter.py -q` 全绿；`python -m pytest tests/test_agent.py tests/test_agent_resume.py -q` 既有无回归。

## T3: subagents/loader.py 四级加载

**文件：** `codepilot/subagents/loader.py`、`tests/subagents/test_loader.py`
**依赖：** T1
**步骤：**
1. 复制 skills/loader.py 的 `_split_frontmatter` 结构与 `_coerce_tools` 思路；新增 `_coerce_blocked`（同 tools 语义）。
2. `parse_role_file(path)`：frontmatter 缺/非法、缺 name/description → `RoleError`；`tools/blocked` 空 → frozenset()；`can_delegate` 默认 False；`permission` 仅接受 manual/auto/strict/permissive（非法抛错）。
3. `discover_role_dirs(workspace, *, user_root=None, plugin_root=None, builtin_root=None)` → 顺序 `[插件, 内置, 用户, 项目]`（优先级从低到高），参考 skills/resolver.py 的 `_SKIP` 剪枝与项目递归。
4. `load_all(workspace, *, check_tools=None)` → `dict[str, AgentRole]`，同名后者覆盖；坏文件 `logger.warning` 跳过；`check_tools` 校验白名单存在性，缺失抛 `RoleError`。
5. 测试：四级优先级覆盖、坏文件隔离、校验全集（缺 name / 非法 permission / 未知工具白名单）、空目录忽略。

**验证：** `python -m pytest tests/subagents/test_loader.py -q` 全绿。

## T4: subagents/runner.py 执行器 + 防线纯函数

**文件：** `codepilot/subagents/runner.py`、`tests/subagents/test_runner.py`、`tests/subagents/test_filters.py`
**依赖：** T1、T2
**步骤：**
1. 顶层纯函数 `build_tool_filter(*, role, depth, max_depth, background, inherit_plan)` → `Callable[[list[Tool]], list[Tool]]`，防线顺序（对齐 D2/D8/D9）：
   - 白名单：`tools` 非空 → 保留 `(白名单 ∪ system_exempt)`；
   - 黑名单：移除 `blocked` 中的工具；
   - agent 禁：`"agent" in [t.name]` 且 `not (role.can_delegate and depth < max_depth)` → 移除 agent；
   - 后台白名单（background）：移除 `side_effect` 工具（只读子集）；
   - plan 继承（inherit_plan）：移除 `side_effect` 工具。
2. `run_role_agent(role, prompt, *, depth, shared)`：构造触发消息 `[{"role":"user","content":f"请以角色「{role.name}」执行：{prompt}"}]`；构造独立 Agent（共享 provider/registry/workspace/env/hooks；独立 permission/context_manager；`skill_injector=lambda: [role.body]`；`tool_filter=build_tool_filter(...)`；`max_rounds=role.max_rounds or 默认`）；`async for` 消费 Delta 收集 text，收集末次 usage；返回 `(summary, usage)`。
3. `run_fork_agent(prompt, *, parent_msgs, parent_tools, depth, shared)`：深拷贝 `parent_msgs` + 追加触发消息；`parent_tools` 作初始全量；system 标准构建（不注入 volatile 块）；同上构造。
4. `shared` 为 dataclass 或 dict：provider/registry/workspace/env/ctx_config/shared_hooks/parent_permission/notify。独立 permission 用角色 permission 字符串 → `PermissionMode`；`human_callback` 透传 `parent_permission.human_callback`。
5. 独立 context_manager：`ContextManager(provider=provider, config=ctx_config)`（不 attach_chat）。
6. 测试（mock provider）：定义式摘要拼接、Fork 历史深拷贝不共享引用、防线四场景各自命中。

**验证：** `python -m pytest tests/subagents/test_runner.py tests/subagents/test_filters.py -q` 全绿。

## T5: subagents/manager.py 委派中心

**文件：** `codepilot/subagents/manager.py`、`tests/subagents/test_manager.py`
**依赖：** T4
**步骤：**
1. `SubAgentManager.__init__(known, *, workspace, provider, registry, env, ctx_config, shared_hooks, parent_permission, max_concurrent=4, max_depth=1, notify=None, on_task_done=None)`。
2. 维护：`_next_id`、`_tasks: dict[int, SubAgentTask]`、`_running: set[int]`、`_depth_ctx: contextvars.ContextVar[int]`（默认 0）。
3. `delegate(kind, prompt, *, role=None, background=False, timeout=None) -> ToolResult`：
   - defined 且 role 未知 → `ToolResult(error=f"未知角色: {role}")`；fork → `role=None`。
   - 后台数量 `>= max_concurrent` → `ToolResult(error=f"后台任务并发已满({max_concurrent})")`。
   - 前台（非 background 且非 fork）：`_depth_ctx.set(depth)` 内 await runner；`timeout` 指定时 `asyncio.wait_for`，超时转后台（D11）；返回 `ToolResult(output=summary)`。
   - 后台：创建 `asyncio.create_task` 执行 runner，登记 task（status=running），返回 `ToolResult(output=f"已转后台，task_id={id}，状态=running")`。
   - 完成/失败：更新 task.status + summary/usage/error；调用 `on_task_done(task)`。
4. `cancel(task_id)`：取消后台 task → status=cancelled。`background_now(task_id)`：把「前台等待中」的 task 移入后台集合并解除 await（D12），status=backgrounded。
5. `tasks()` 返回快照列表。
6. 测试：并发超限拒绝、深度递增（`_depth_ctx` 值）、前台完成摘要、后台完成 on_task_done 回调、cancel、background_now、未知角色。

**验证：** `python -m pytest tests/subagents/test_manager.py -q` 全绿。

## T6: subagents/tool.py agent 工具

**文件：** `codepilot/subagents/tool.py`、`tests/subagents/test_tool.py`
**依赖：** T5
**步骤：**
1. `class AgentTool(Tool)`：`name="agent"`；`description` 说明 defined/fork 两种用法；`system_exempt=True`；`side_effect=False`。
2. `parameters`：`mode`(enum defined/fork, 必填)、`role`(str, defined 时用)、`prompt`(str, 必填)、`background`(bool, default False)、`timeout`(number, 可选)。
3. `execute(**kwargs) -> ToolResult`：参数校验（缺 mode/prompt → `ToolResult(error=...)`）；`background` 显式转后台；其余转发 `self._manager.delegate(...)`；**整个方法 try/except，异常转 `ToolResult(error=...)` 永不外抛**。
4. 测试：参数校验、defined/fork 分流、background 透传、异常兜底。

**验证：** `python -m pytest tests/subagents/test_tool.py -q` 全绿。

## T7: 包导出 + 内置角色 + package-data

**文件：** `codepilot/subagents/__init__.py`、`codepilot/subagents/data/architect.md`、`pyproject.toml`
**依赖：** T1–T6
**步骤：**
1. `__init__.py` 导出 `SubAgentManager / AgentRole / AgentTool / RoleError / load_all`。
2. `architect.md`：frontmatter `name: architect / description / tools: [read, glob, grep] / can_delegate: true`，正文定义「架构师」身份与工作风格（可放行 agent 作样板）。
3. `pyproject.toml` 的 `[tool.setuptools.package-data]` 加 `"subagents/data/*.md"`。

**验证：** `python -c "from codepilot.subagents import SubAgentManager, AgentRole, AgentTool, load_all"` 导入成功；`python -m pytest tests/subagents/test_loader.py -q` 仍全绿（load_all 读到内置 role）。

## T8: commands/builtins.py 注册 /agents /bg

**文件：** `codepilot/commands/builtins.py`、`codepilot/commands/context.py`
**依赖：** T5（仅类型引用，可并行）
**步骤：**
1. `h_agents(ctx, params)`：`mgr = getattr(ctx, "subagent_manager", None)`；None → `ctx.show("SubAgent 未启用")`；否则列出任务表（id/kind/role/status/usage）。
2. `h_bg(ctx, params)`：同上取 mgr；解析 `params` 为 task_id，调 `mgr.background_now(task_id)`；返回布尔 → show 成功/失败。
3. `_register("agents", CommandKind.LOCAL, "列出后台子 Agent 任务", h_agents)`；`_register("bg", CommandKind.LOCAL, "把前台子 Agent 任务切后台", h_bg, usage="<task_id>")`。
4. context.py 的 `CommandContext` 协议**不加**方法（沿用 state15 getattr 惯例，N3 向后兼容）——若想类型提示，可在协议注释注明。

**验证：** `python -m pytest tests/test_commands.py tests/test_commands_builtins.py -q` 全绿；`python -c "from codepilot.commands import build_registry; r=build_registry(); assert r.get('agents') and r.get('bg')"`。

## T9: ui/app.py 接线（回灌 + context 挂载）

**文件：** `codepilot/ui/app.py`
**依赖：** T8
**步骤：**
1. `CodePilotApp.__init__` 新增可选参数 `subagent_manager=None`，存 `self._subagent_manager`。
2. `on_mount` 末尾：若 `self._subagent_manager` 非空，把 `_CommandContextImpl` 的 `subagent_manager` 属性指向它（state15 `run_isolated_skill` 同款手法）。
3. `_on_subagent_done(task)`：写系统行 `[subagent] #N <role> 完成（tokens=…）`（复用 `_hook_notify` 同款写 ChatLog）；摘要作为一条 `user` 消息追加 `self._chat._msgs`（D13，不触发新一轮）。
4. `on_mount` 里 `self._subagent_manager.on_task_done = self._on_subagent_done`。

**验证：** `python -c "import codepilot.ui.app"` 导入成功；`python -m pytest tests/test_completion_screen.py -q` 全绿（TUI 装配无回归）。

## T10: main.py 装配

**文件：** `codepilot/main.py`
**依赖：** T6、T9
**步骤：**
1. hook_engine 构建后：`from codepilot.subagents import ...`；`role_map = load_all(workspace, check_tools=lambda n: registry.get(n) is not None)`。
2. `subagent_manager = SubAgentManager(role_map, workspace=..., provider=..., registry=..., env=..., ctx_config=ctx_config, shared_hooks=hook_engine, parent_permission=permission)`。
3. `registry.register(AgentTool(subagent_manager))`（系统级，主 Agent 常驻可见）。
4. `CodePilotApp(..., subagent_manager=subagent_manager)`。
5. `app.on_mount` 前无需额外接线（T9 已做）。

**验证：** `python -c "import codepilot.main"` 导入成功；`python -m pytest tests/ -q` 全量全绿（新增注册不破坏既有）。

## T11: 全量回归 + lint + 收口

**文件：** 无新文件
**依赖：** T1–T10
**步骤：**
1. `python -m pytest -q` 全量测试通过（既有 482 + 新增全绿）。
2. `ruff check codepilot/subagents tests/subagents` 零告警；`ruff check .` 仅既有基线不新增。
3. 更新 `SHARED_PROGRESS.md` 与 `docs/state17-subagent/checklist.md`（阶段四产出）对应条目。

**验证：** 以上命令输出符合预期。

## 执行顺序

```
T1 → T2 ─┐
         ├→ T3 → T4 → T5 → T6 → T7 → T10 → T11
T2(T1) ──┘
         T8(T5 引用，可并行) → T9(T8) ─────────────┘
```

- T1 与 T2 可并行（T2 不依赖 subagents 包）。
- T8 仅类型引用 T5，可与 T3–T6 并行推进；T9 依赖 T8。
- T10 在 T6/T9 完成后进行；T11 收口。
