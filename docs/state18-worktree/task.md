# Worktree 工作目录隔离 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `codepilot/worktree/__init__.py` | 包导出 |
| 新建 | `codepilot/worktree/model.py` | Meta/Handle/Entry/ExitResult/CleanupReport |
| 新建 | `codepilot/worktree/naming.py` | 名称校验与自动命名 |
| 新建 | `codepilot/worktree/settings.py` | `.worktree.yaml` 读取与校验 |
| 新建 | `codepilot/worktree/git.py` | Git 子进程封装 |
| 新建 | `codepilot/worktree/meta.py` | 元数据 JSON 读写 |
| 新建 | `codepilot/worktree/initializer.py` | copy/symlink/include/hooks |
| 新建 | `codepilot/worktree/manager.py` | 生命周期、锁、保留决策、清理 |
| 新建 | `codepilot/subagents/isolation.py` | 隔离子 Agent 运行时装配 |
| 修改 | `codepilot/subagents/model.py` | `AgentRole.isolation`、`SubAgentTask.worktree` |
| 修改 | `codepilot/subagents/loader.py` | isolation 解析、扫描剪枝 |
| 修改 | `codepilot/subagents/tool.py` | `worktree` 参数 |
| 修改 | `codepilot/subagents/runner.py` | 隔离运行时接入 |
| 修改 | `codepilot/subagents/manager.py` | enter/leave/摘要 |
| 修改 | `codepilot/commands/builtins.py` | `/worktree` 命令 |
| 修改 | `codepilot/ui/app.py` | worktree_manager 注入 |
| 修改 | `codepilot/main.py` | 装配 + 后台清理 |
| 修改 | `codepilot/memory/resolver.py` | 扫描剪枝 |
| 修改 | `codepilot/skills/resolver.py` | 扫描剪枝 |
| 修改 | `codepilot/tools/glob_tool.py` | 结果过滤 |
| 修改 | `codepilot/tools/grep_tool.py` | 结果过滤 |
| 新建 | `tests/worktree/__init__.py` | 测试包 |
| 新建 | `tests/worktree/conftest.py` | 临时 Git 仓库 fixture |
| 新建 | `tests/worktree/test_naming.py` | 名称校验 |
| 新建 | `tests/worktree/test_settings.py` | 配置读取 |
| 新建 | `tests/worktree/test_git.py` | Git 封装 |
| 新建 | `tests/worktree/test_meta.py` | 元数据 |
| 新建 | `tests/worktree/test_initializer.py` | 环境初始化 |
| 新建 | `tests/worktree/test_manager.py` | 生命周期 |
| 新建 | `tests/subagents/test_isolation.py` | 隔离运行时 |
| 新建 | `tests/test_glob_worktree.py` | glob 扫描隔离 |
| 新建 | `tests/test_grep_worktree.py` | grep 扫描隔离 |
| 修改 | `tests/subagents/test_loader.py` | isolation 解析 |
| 修改 | `tests/subagents/test_tool.py` | worktree 参数 |
| 修改 | `tests/subagents/test_manager.py` | enter/leave 集成 |
| 修改 | `tests/subagents/test_runner.py` | 隔离运行时 |
| 修改 | `tests/test_commands_builtins.py` | `/worktree` 命令 |
| 修改 | `tests/test_memory_resolver.py` | `.codepilot` 剪枝 |
| 修改 | `tests/test_skills_resolver.py` | `.codepilot` 剪枝 |

## T1: worktree 包骨架与数据模型

**文件：** `codepilot/worktree/__init__.py`、`codepilot/worktree/model.py`

**依赖：** 无

**步骤：**
1. 创建包目录与 `__init__.py`（模块 docstring，暂不导出）。
2. 在 `model.py` 定义 `WorktreeMeta`、`WorktreeHandle`、`WorktreeEntry`、`ExitResult`、`CleanupReport`，字段按 plan。
3. 所有 dataclass 使用 `frozen=True`；`CleanupReport` 的 `skipped` 类型为 `tuple[tuple[str, str], ...]`。

**验证：**
```bash
python -c "from codepilot.worktree.model import WorktreeMeta, WorktreeHandle, WorktreeEntry, ExitResult, CleanupReport; print('ok')"
```

## T2: 名称校验与自动命名

**文件：** `codepilot/worktree/naming.py`、`tests/worktree/__init__.py`、`tests/worktree/test_naming.py`

**依赖：** T1

**步骤：**
1. 实现 `WorktreeNameError`、`validate_name`、`auto_name`。
2. 校验：总长 ≤64、≤3 段、段长 1–32、`[a-z0-9._-]`、段首字母/数字、拒绝 `.`/`..`/空段/反斜杠/绝对路径/前后空白/段尾点/段尾空格/Windows 保留名。
3. `auto_name`：角色名 slug 化，追加 `-<task_id>`，再调用 `validate_name`。
4. 测试覆盖合法嵌套名、每类非法输入、保留名（含 `CON.txt`）、自动命名。

**验证：**
```bash
python -m pytest tests/worktree/test_naming.py -q
```

## T3: `.worktree.yaml` 配置读取

**文件：** `codepilot/worktree/settings.py`、`tests/worktree/test_settings.py`

**依赖：** T1

**步骤：**
1. 定义 `WorktreeSettings` 默认值与 `WorktreeSettingsError`。
2. 实现 `load_settings(workspace)`：读 `<workspace>/.worktree.yaml` 的 `worktree:` 段；文件缺失返回默认。
3. 校验：`ttl_hours > 0`、`cleanup_interval_minutes > 0`、列表字段为字符串列表、规则不得绝对路径或含 `..` 段。
4. `copy/symlink/include` 写了即整体覆盖默认列表。
5. 测试：缺文件、合法覆盖、非法类型、非法 TTL、逃逸规则。

**验证：**
```bash
python -m pytest tests/worktree/test_settings.py -q
```

## T4: Git 命令封装

**文件：** `codepilot/worktree/git.py`、`tests/worktree/conftest.py`、`tests/worktree/test_git.py`

**依赖：** T1

**步骤：**
1. 在 `conftest.py` 提供 `git_repo` fixture：临时目录 `git init`、配置 user、提交一个文件；无 Git 环境时 `pytest.skip`。
2. 实现 `GitError` 与 `GitRunner` 的方法（toplevel/head/branch_exists/add_worktree/attach_worktree/remove_worktree/delete_branch/status_porcelain/commits_ahead/merge_base/is_ignored/hooks_path/set_worktree_config）。
3. 所有调用使用参数列表、`shell=False`、显式 `cwd`、`capture_output=True`；非零退出抛 `GitError`。
4. 测试：HEAD、分支存在性、新建/挂接/删除 worktree、dirty 状态、ahead 计数、merge-base、check-ignore、错误信息。

**验证：**
```bash
python -m pytest tests/worktree/test_git.py -q
```

## T5: 元数据读写

**文件：** `codepilot/worktree/meta.py`、`tests/worktree/test_meta.py`

**依赖：** T1、T4（fixture 可复用）

**步骤：**
1. 实现 `meta_path`（`_meta/<sha1(name)>.json`）、`read_meta`、`write_meta`（临时文件 + `os.replace` 原子写）、`delete_meta`、`iter_metas`。
2. 损坏 JSON / 缺字段时返回 `None` 或跳过，记日志，不抛。
3. 测试：写后读一致、删除幂等、多条目遍历、损坏文件跳过、名称哈希稳定。

**验证：**
```bash
python -m pytest tests/worktree/test_meta.py -q
```

## T6: 环境初始化（copy / symlink / include）

**文件：** `codepilot/worktree/initializer.py`、`tests/worktree/test_initializer.py`

**依赖：** T3、T4

**步骤：**
1. 实现 `WorktreeInitError` 与 `Initializer.initialize` 的前三步。
2. 规则 glob 扩展：`copy` 复制文件/目录；`include` 仅复制 `git.is_ignored` 命中的匹配项；`symlink` 只处理目录。
3. 目标已存在跳过；源不存在跳过并记日志；匹配路径 `resolve()` 必须仍在主仓内；IO 失败抛 `WorktreeInitError`。
4. Windows 目录软链失败时回退 `cmd /c mklink /J`。
5. 测试：配置复制、忽略文件补齐、依赖软链、不覆盖、源缺失跳过、逃逸拒绝、IO 失败。

**验证：**
```bash
python -m pytest tests/worktree/test_initializer.py -q
```

## T7: Git hooks 配置

**文件：** `codepilot/worktree/initializer.py`、`tests/worktree/test_initializer.py`

**依赖：** T6

**步骤：**
1. 实现 `_configure_hooks(wt_path)`。
2. 主仓存在 `.githooks/`：在 worktree 建软链，启用 `extensions.worktreeConfig`，写 worktree 级 `core.hooksPath` 为主仓绝对路径。
3. 否则 `core.hooksPath` 为相对路径：解析为主仓绝对路径并写入 worktree 级配置。
4. 其余情况无操作。
5. 测试：`.githooks` 场景、相对 hooksPath 场景、默认无操作、配置写入后 `git config --worktree --get` 可读。

**验证：**
```bash
python -m pytest tests/worktree/test_initializer.py -q
```

## T8: WorktreeManager 骨架与快速恢复

**文件：** `codepilot/worktree/manager.py`、`tests/worktree/test_manager.py`

**依赖：** T2、T3、T4、T5

**步骤：**
1. 实现 `WorktreeError`、`WorktreeManager.__init__`（`main_root`、`settings`、`git`、`root`、`_lock: RLock`、`_active: dict[str, int]`）。
2. 实现 `enter` 的公共前置：主仓根 == Git toplevel 校验、`validate_name`、路径包含校验、占用锁。
3. 实现目录已存在时的三检（目录、`.git` 是文件、元数据名字匹配）；通过返回 `reused=True`，失败抛错不接管。
4. 测试：合法复用、陌生目录拒绝、元数据缺失拒绝、占用冲突、非法名称、非仓库根报错；用会抛错的 fake Git 证明快速恢复不调用 Git。

**验证：**
```bash
python -m pytest tests/worktree/test_manager.py -q
```

## T9: 创建、挂接与失败清理

**文件：** `codepilot/worktree/manager.py`、`tests/worktree/test_manager.py`

**依赖：** T8、T6、T7

**步骤：**
1. 目录不存在且分支不存在：记录 `head()`、`add_worktree(path, branch, base)`、写元数据、初始化环境。
2. 分支存在：`attach_worktree`；base 优先取元数据，否则 `merge_base(HEAD, branch)`，失败回退 HEAD。
3. 任一步失败：删除本次新建的目录/分支/元数据，释放占用锁，包装为 `WorktreeError`。
4. 测试：新建分支与 base 正确、自动名称、挂接已有分支、被其他 worktree 占用时报错、初始化失败无残留、锁释放。

**验证：**
```bash
python -m pytest tests/worktree/test_manager.py -q
```

## T10: 保留判定、leave 与删除保护

**文件：** `codepilot/worktree/manager.py`、`tests/worktree/test_manager.py`

**依赖：** T9

**步骤：**
1. 实现纯函数 `decide_retention(dirty, ahead)`。
2. 实现 `leave(handle)`：查 dirty/ahead → 决定保留或删除目录+分支+元数据 → 释放锁；清理异常只记日志。
3. 实现 `remove(name, force=False)`：占用/陌生目录拒绝；非 force 有变更拒绝；force 删目录保分支。
4. 测试：clean 删除目录+分支、dirty 保留、ahead 保留、`remove` 拒绝、`--force` 保留分支、锁释放。

**验证：**
```bash
python -m pytest tests/worktree/test_manager.py -q
```

## T11: 列表、过期清理与周期任务

**文件：** `codepilot/worktree/manager.py`、`tests/worktree/test_manager.py`

**依赖：** T10

**步骤：**
1. 实现 `list_status()`：列出元数据对应目录的 branch/dirty/ahead/locked_by。
2. 实现 `cleanup_expired(now=None)`：TTL 过滤 + 三层安全过滤（路径包含 → 元数据/`.git`/分支前缀 → 未占用/clean/ahead==0）。
3. 单项异常记录到 `skipped`，不中断整体。
4. 实现 `run_periodic_cleanup()`：立即清理一次，之后按 `cleanup_interval_minutes` 循环；用 `asyncio.to_thread` 调同步实现。
5. 测试：过期 clean 删除、过期 dirty 跳过、陌生目录跳过、占用跳过、报告内容、短周期任务可取消。

**验证：**
```bash
python -m pytest tests/worktree/test_manager.py -q
```

## T12: 递归发现剪枝 `.codepilot`

**文件：** `codepilot/memory/resolver.py`、`codepilot/skills/resolver.py`、`codepilot/subagents/loader.py`、`tests/test_memory_resolver.py`、`tests/test_skills_resolver.py`、`tests/subagents/test_loader.py`

**依赖：** 无（可与 T1–T11 并行）

**步骤：**
1. 三处 `os.walk` 的剪枝集合加入 `.codepilot`。
2. 在临时工作区创建 `.codepilot/worktrees/x/.rules/`、`.skills/`、`.agents/`，断言主发现结果不包含它们。
3. 保持原有剪枝行为（`.git`、`node_modules` 等）不变。

**验证：**
```bash
python -m pytest tests/test_memory_resolver.py tests/test_skills_resolver.py tests/subagents/test_loader.py -q
```

## T13: glob / grep 跳过 Worktree 根

**文件：** `codepilot/tools/glob_tool.py`、`codepilot/tools/grep_tool.py`、`tests/test_glob_worktree.py`、`tests/test_grep_worktree.py`

**依赖：** 无

**步骤：**
1. 在两个工具的结果收集处判断相对路径：以 `.codepilot/worktrees/` 开头的一律跳过。
2. 不改变 `rglob` 本身与既有 `.git` 过滤语义。
3. 测试：工作区根有正常文件、worktree 根有同名/匹配文件时，结果只含正常文件；无 worktree 时结果与旧行为一致。

**验证：**
```bash
python -m pytest tests/test_glob_worktree.py tests/test_grep_worktree.py tests/test_tools_glob.py tests/test_tools_grep.py -q
```

## T14: 角色 `isolation` 字段

**文件：** `codepilot/subagents/model.py`、`codepilot/subagents/loader.py`、`tests/subagents/test_loader.py`

**依赖：** 无

**步骤：**
1. `AgentRole` 增加 `isolation: str | None = None`。
2. `parse_role_text` 解析 `isolation`：缺省 `None`；`worktree` 合法；其他值抛 `RoleError`。
3. 测试：缺省、合法、非法值、大小写/空白处理（统一小写去空白）。

**验证：**
```bash
python -m pytest tests/subagents/test_loader.py -q
```

## T15: 隔离运行时装配

**文件：** `codepilot/subagents/isolation.py`、`tests/subagents/test_isolation.py`

**依赖：** T1、T14

**步骤：**
1. 实现 `build_isolated_registry(template, workspace)`：六个文件/命令工具重新实例化，其余工具共享。
2. 实现 `isolation_prompt_block(handle)`。
3. 实现 `build_isolated_runtime(shared, role, handle)`：provider/role permission/env/context offload/memory/skill_injector。
4. 测试：registry 中 read/write/run 的 `workspace` 指向 worktree、agent/load_skill 实例与模板相同；env.cwd 与 offload_dir 正确；memory 从 worktree 读取；prompt 块含路径与分支。

**验证：**
```bash
python -m pytest tests/subagents/test_isolation.py -q
```

## T16: runner 接入隔离运行时

**文件：** `codepilot/subagents/runner.py`、`tests/subagents/test_runner.py`

**依赖：** T15

**步骤：**
1. `run_role_agent(..., worktree: WorktreeHandle | None = None)`。
2. 传入句柄时用 `build_isolated_runtime` 的 provider/registry/workspace/env/permission/context_manager/skill_injector 构造 Agent。
3. 未传句柄时执行路径与现状完全一致；`run_fork_agent` 不新增参数。
4. 测试：传句柄时 Agent 的 workspace/env/offload 指向 worktree；不传时行为不变（现有测试不修改断言即可通过）。

**验证：**
```bash
python -m pytest tests/subagents/test_runner.py -q
```

## T17: agent 工具与委派中心接入

**文件：** `codepilot/subagents/tool.py`、`codepilot/subagents/manager.py`、`tests/subagents/test_tool.py`、`tests/subagents/test_manager.py`

**依赖：** T14、T16

**步骤：**
1. `AgentTool.parameters` 增加 `worktree`；`execute` 透传。
2. `SubAgentManager.__init__` 注入 `worktree: WorktreeManager | None`，公开属性。
3. `delegate(..., worktree=None)`：校验仅隔离定义式角色可用；`task.worktree` 记录名称（缺省 `auto_name`）；非隔离/fork 传参返回错误。
4. `_run_one`：隔离任务 `to_thread(enter)` → 传句柄给 runner → `finally` 中 shield `to_thread(leave)`，把保留/清理结果追加到摘要/错误；锁释放。
5. `role_list_block` 标注隔离角色。
6. 测试：worktree 参数透传、缺省自动名、非隔离拒绝、enter 失败任务失败、leave 保留信息进摘要、取消路径仍尝试 leave。

**验证：**
```bash
python -m pytest tests/subagents/test_tool.py tests/subagents/test_manager.py -q
```

## T18: `/worktree` 命令与 App 注入

**文件：** `codepilot/commands/builtins.py`、`codepilot/ui/app.py`、`tests/test_commands_builtins.py`

**依赖：** T11、T17

**步骤：**
1. `CodePilotApp.__init__` 增加 `worktree_manager=None`；`_CommandContextImpl` 增加 `worktree_manager` 属性。
2. `builtins.py` 新增 LOCAL 命令 `worktree`，支持 `list` 与 `rm <name> [--force]`，manager 缺失时安全降级。
3. 输出包含名称、分支、路径、占用、dirty、ahead。
4. 更新内置命令全集断言（新增 `worktree`）。
5. 测试：list 输出、rm 成功/拒绝/force、未启用降级、命令集不冲突。

**验证：**
```bash
python -m pytest tests/test_commands_builtins.py -q
```

## T19: main 装配与后台清理

**文件：** `codepilot/main.py`

**依赖：** T17、T18

**步骤：**
1. 启动时 `load_settings(workspace)`；解析失败打印错误并退出（fail-fast）。
2. 构造 `WorktreeManager(workspace, settings)`，注入 `SubAgentManager` 与 `CodePilotApp`。
3. `_run_ui` 中创建 `asyncio.create_task(worktree_manager.run_periodic_cleanup())`；退出时取消并回收。
4. 确保未启用隔离时行为与现状一致。

**验证：**
```bash
python -c "import codepilot.main; print('ok')"
```

## T20: 全量回归与 lint

**文件：** 无新增

**依赖：** T1–T19

**步骤：**
1. 运行全量测试，修复所有回归。
2. 运行 `ruff check codepilot/worktree codepilot/subagents tests/worktree tests/subagents`，清零新文件告警。
3. 运行 `ruff check .`，确认仍是 27 条既有基线，不新增。
4. 记录最终测试数量。

**验证：**
```bash
python -m pytest -q
ruff check codepilot/worktree codepilot/subagents tests/worktree tests/subagents
ruff check .
```

## 执行顺序

```text
T1 → T2 → T3 → T4 → T5 → T6 → T7
                              ↓
T8 → T9 → T10 → T11 ──┐
                       ├→ T17 → T18 → T19 → T20
T14 → T15 → T16 ───────┘
T12 / T13（可并行）
```

- T12、T13 不依赖 worktree 领域层，可在 T1–T11 期间并行完成。
- T14–T16 只依赖 worktree 数据模型（T1），可在 T8–T11 完成前并行推进。
- T17 是两条线的汇合点；T18–T20 必须串行。
