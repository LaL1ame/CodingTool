# Team Lead 长期小组 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `codepilot/teams/__init__.py` | 包导出 |
| 新建 | `codepilot/teams/model.py` | 枚举、数据类、错误类型 |
| 新建 | `codepilot/teams/naming.py` | 小组/成员名校验、worktree 名映射 |
| 新建 | `codepilot/teams/settings.py` | TeamSettings 与后端选择 |
| 新建 | `codepilot/teams/locks.py` | FileLock |
| 新建 | `codepilot/teams/storage.py` | 路径布局与原子 IO |
| 新建 | `codepilot/teams/tasks.py` | TaskBoard 与依赖纯函数 |
| 新建 | `codepilot/teams/registry.py` | MemberRegistry |
| 新建 | `codepilot/teams/mailbox.py` | 消息校验/摘要/读写 |
| 新建 | `codepilot/teams/prompts.py` | 团队提示文本 |
| 新建 | `codepilot/teams/runtime.py` | MemberRuntime |
| 新建 | `codepilot/teams/tools_member.py` | 成员协作工具 |
| 新建 | `codepilot/teams/tools_lead.py` | Lead 编排工具 |
| 新建 | `codepilot/teams/manager.py` | TeamManager |
| 修改 | `codepilot/tools/registry.py` | `clone()` |
| 修改 | `codepilot/worktree/manager.py` | `release(handle)` |
| 修改 | `codepilot/main.py` | 装配 |
| 修改 | `codepilot/mcp/manager.py` | MCP 工具同步写入 Lead registry |
| 修改 | `codepilot/ui/app.py` | 事件回灌与命令上下文 |
| 修改 | `codepilot/commands/builtins.py` | `/team` 命令 |
| 修改 | `config.example.yaml` | `team:` 示例 |
| 新建 | `tests/teams/*` | 单元、集成、端到端测试 |
| 新建 | `tests/test_team_app.py` | App 事件回灌测试 |
| 新建 | `tests/teams/test_wiring.py` | main 装配测试 |
| 新建 | `tests/teams/test_model.py` | 模型测试 |
| 新建 | `tests/teams/test_prompts.py` | 提示块测试 |

> 注：plan 中的 `StrEnum` 在 task 中实现为 `class X(str, Enum)`，因为项目声明
> `requires-python >=3.10` 而 `StrEnum` 是 3.11+；行为等价。

## T1：包骨架与数据模型

**文件：** `codepilot/teams/__init__.py`、`codepilot/teams/model.py`、`tests/teams/__init__.py`、`tests/teams/test_model.py`
**依赖：** 无

**步骤：**
1. 创建包目录；`__init__.py` 暂只写 docstring。
2. 用 `class X(str, Enum)` 定义 `TeamMemberStatus`、`TeamMemberBackend`、`TaskStatus`、`TeamMessageType`。
3. 定义错误层级：`TeamError` 及 7 个子类。
4. 定义 frozen dataclass：`TeamSettings`、`TeamMeta`、`TeamMember`、`MemberRef`、`Task`、`TeamMessage`、`TeamEvent`，字段按 plan。
5. 测试枚举值、dataclass 不可变、错误继承关系。

**验证：** `python -m pytest tests/teams/test_model.py -q`

## T2：命名校验与 worktree 名映射

**文件：** `codepilot/teams/naming.py`、`tests/teams/test_naming.py`
**依赖：** T1

**步骤：**
1. 实现 `validate_team_name/validate_member_name`：1–24 字符、单段、小写、
   字符集 `[a-z0-9._-]`、首字符字母/数字、拒绝 `.`/`..`、段尾点/空格、保留名。
2. 成员名 `lead` 保留。
3. `member_worktree_name(team, member)` 生成 `team/<team>/<member>`，再调用
   `worktree.naming.validate_name` 校验。
4. 表驱动测试合法/非法输入、长度边界、大小写、保留名、worktree 名长度。

**验证：** `python -m pytest tests/teams/test_naming.py -q`

## T3：TeamSettings 与后端选择

**文件：** `codepilot/teams/settings.py`、`tests/teams/test_settings.py`
**依赖：** T1

**步骤：**
1. 实现默认值与 `load_team_settings(config_path)`：读取 `team:` 段，缺文件/缺段用默认。
2. 校验：`root` 展开 `~`；数值 > 0；`default_backend` 为字符串。
3. 实现 `select_backend(requested, settings)`：`auto`/`inprocess` 通过；
   `terminal` 与未知值抛 `TeamBackendError`。
4. 测试缺文件、合法覆盖、非法数值、根路径展开、三种后端取值。

**验证：** `python -m pytest tests/teams/test_settings.py -q`

## T4：FileLock

**文件：** `codepilot/teams/locks.py`、`tests/teams/test_locks.py`
**依赖：** T1

**步骤：**
1. 实现 `FileLock` 上下文管理器：`O_CREAT|O_EXCL`、写入 pid/token、重试、
   mtime 过期接管、超时抛 `TeamLockError`。
2. 释放前校验 token，不匹配不删除。
3. 注入 `clock/sleep`；测试使用临时目录与 `os.utime` 制造过期锁。
4. 测试：正常获取/释放、被占用时等待后超时、过期锁接管、token 不匹配不误删、
   异常路径释放。

**验证：** `python -m pytest tests/teams/test_locks.py -q`

## T5：持久化布局与原子 IO

**文件：** `codepilot/teams/storage.py`、`tests/teams/test_storage.py`
**依赖：** T1、T3

**步骤：**
1. 实现 `TeamStorage` 路径方法：根目录、小组目录、team/registry/tasks/members/mailboxes/locks 路径。
2. `atomic_write_json/read_json`：临时文件 + `os.replace`，`ensure_ascii=False`。
3. `read_jsonl/rewrite_jsonl`：坏行跳过并计数；原子重写。
4. 损坏 `tasks.json` 隔离：备份为 `.corrupt-<ts>` 并返回空结构（由调用方触发）。
5. 测试：写入后可读、覆盖原子性、坏 JSONL 行跳过、损坏任务文件备份、
   路径不逃逸小组目录。

**验证：** `python -m pytest tests/teams/test_storage.py -q`

## T6：任务板

**文件：** `codepilot/teams/tasks.py`、`tests/teams/test_tasks.py`
**依赖：** T4、T5

**步骤：**
1. 实现 `TaskBoard`：`create/list/get/update/delete/claim`，每次操作在 team lock 内
   读改写 `tasks.json`。
2. 创建校验：标题非空、依赖存在/去重/拒绝自依赖、assignee 合法。
3. `claim`：pending、未指派、依赖全部 done → 写 assignee；返回具体失败原因。
4. 状态机与删除保护：被依赖的、in_progress 的任务不可删；完成写 result。
5. 纯函数 `deps_satisfied` 返回未完成依赖列表。
6. 测试：CRUD、依赖链、并发领取（线程/进程内并发）只有一个成功、非法转移、
   删除保护。

**验证：** `python -m pytest tests/teams/test_tasks.py -q`

## T7：名称注册表

**文件：** `codepilot/teams/registry.py`、`tests/teams/test_registry.py`
**依赖：** T4、T5、T6（任务仅用于离线标记测试）

**步骤：**
1. 实现 `MemberRegistry.resolve/resolve_active/list/update/rebuild`。
2. `lead` 返回合成引用；成员从 `members/*.json` 加载；terminated 在 `resolve_active` 拒绝。
3. `update` 在 team lock 内同时写成员元数据与 `registry.json`。
4. `rebuild` 从成员文件重建索引；缺失/损坏/不一致时自动调用。
5. 小组加载时把非 terminated 成员置 offline 并持久化（提供 `mark_all_offline`）。
6. 测试：解析 lead/成员、未知/terminated 拒绝、重建、离线标记、并发更新。

**验证：** `python -m pytest tests/teams/test_registry.py -q`

## T8：邮箱与消息协议

**文件：** `codepilot/teams/mailbox.py`、`tests/teams/test_mailbox.py`
**依赖：** T4、T5

**步骤：**
1. 消息构造/校验：五类必填字段、状态值、自动 id/timestamp/read/summary。
2. `append`：邮箱锁内单行写入 + flush/fsync；失败不产生半行。
3. `consume_unread(limit)`、`consume_matching(type, task_id, limit)`、
   `peek()`；标记已读并原子重写。
4. 坏行跳过并记录；广播副本由调用方逐个邮箱写入。
5. 测试：五类消息往返、缺字段拒绝、摘要截断、未读/已读、匹配消费保留其他未读、
   锁失败抛错、坏行隔离。

**验证：** `python -m pytest tests/teams/test_mailbox.py -q`

## T9：Worktree release 与 Registry clone

**文件：** `codepilot/worktree/manager.py`、`codepilot/tools/registry.py`、`tests/worktree/test_manager.py`、`tests/test_tools_registry.py`
**依赖：** 无（可与 T1–T8 并行）

**步骤：**
1. `WorktreeManager.release(handle)`：仅从 `_active` 移除，不做 Git/文件操作。
2. `ToolRegistry.clone()`：返回共享同一工具实例的新 registry。
3. 测试：release 后同名可再次 enter、目录/分支仍在；clone 后注册新工具不影响原 registry。

**验证：** `python -m pytest tests/worktree/test_manager.py tests/test_tools_registry.py -q`

## T10：团队提示块

**文件：** `codepilot/teams/prompts.py`、`tests/teams/test_prompts.py`
**依赖：** T1

**步骤：**
1. 实现四个纯函数：`lead_context_block`、`member_context_block`、
   `task_execution_prompt`、`task_planning_prompt`。
2. 内容覆盖：当前组、成员状态、任务计数、worktree/分支、审批要求、
   任务边界消息约定、无 Lead 工具提示。
3. 测试：关键字段出现、无组时提示建组、计划提示禁止写文件。

**验证：** `python -m pytest tests/teams/test_prompts.py -q`

## T11：成员运行时骨架与 Agent 装配

**文件：** `codepilot/teams/runtime.py`、`tests/teams/test_runtime.py`、`tests/teams/conftest.py`
**依赖：** T6、T7、T8、T10

**步骤：**
1. 定义 `MemberRuntime` 构造参数与可注入依赖（`agent`/`clock`/`sleep`）。
2. 实现 `start/stop/wake/status`：创建 asyncio task、idle 等待、终止时取消并收尾。
3. Agent 装配辅助：从 base registry 重建 worktree 绑定工具，排除 `agent`、`load_skill`，
   加入成员工具；构造 permission/context/env/memory/团队注入。
4. 计划阶段只读的 tool_filter 闭包（后续 T13 使用）。
5. 测试用 FakeAgent/FakeManager：start→idle、wake→循环、stop→cancelled/terminated、
   不发生 `chdir`。

**验证：** `python -m pytest tests/teams/test_runtime.py -q`

## T12：成员任务执行循环

**文件：** `codepilot/teams/runtime.py`、`tests/teams/test_runtime.py`
**依赖：** T11

**步骤：**
1. 实现循环：消费未读 → 取 assignee=自己且 pending 的任务 → 逐项执行。
2. 执行时任务置 in_progress，追加任务提示到长期 `_msgs`，调用 `agent.run` 并收集文本。
3. 成功：任务 done + result；失败/轮次上限：任务保持 in_progress，成员 failed/blocked
   并通知 Lead；不伪造 done。
4. 无任务无消息 → idle；状态只在变化时上报。
5. 测试：单任务完成、多任务连续、失败路径、轮次上限路径、未读消息注入。

**验证：** `python -m pytest tests/teams/test_runtime.py -q`

## T13：审批闭环

**文件：** `codepilot/teams/runtime.py`、`tests/teams/test_approval.py`
**依赖：** T12

**步骤：**
1. 需审批任务进入计划阶段：切换只读 tool_filter，调用 Agent 生成计划文本；
   计划阶段不得写文件、执行命令、发消息。
2. 发送 `plan_request` 给 Lead（task_id + 计划正文），成员转 blocked 并等待。
3. 等待循环：`consume_matching(plan_decision, task_id)`；批准 → running → 执行回合；
   驳回 → 任务回 pending、清 assignee、idle。
4. 超过 `approval_timeout_seconds`：发一次 `status(blocked)` 通知，继续等待后续 decision；
   不重复刷屏、不自动开工。
5. 测试：批准、驳回、超时后迟到批准、只消费本任务的 decision、计划阶段无写工具。

**验证：** `python -m pytest tests/teams/test_approval.py -q`

## T14：成员协作工具

**文件：** `codepilot/teams/tools_member.py`、`tests/teams/test_tools_member.py`
**依赖：** T6、T7、T8

**步骤：**
1. 实现 8 个工具：`task_list/get/create/update/delete/claim`、
   `send_message`、`broadcast`。
2. `task_update` 不允许改 assignee；claim 失败原因原样返回给模型。
3. 读类工具 `side_effect=False`，写类为 True；异常统一转 `ToolResult(error=...)`。
4. 测试：参数校验、CRUD、依赖门控、claim、消息发送/广播、错误路径。

**验证：** `python -m pytest tests/teams/test_tools_member.py -q`

## T15：TeamManager 小组生命周期

**文件：** `codepilot/teams/manager.py`、`tests/teams/test_manager.py`
**依赖：** T5、T6、T7

**步骤：**
1. 定义 `TeamManager` 构造签名：settings、storage、role map、base registry、
   provider/env/ctx_config/hooks/parent_permission、worktree、可注入 agent 工厂与时钟。
2. 实现 `create_team/list_teams/use_team/current_team`；创建目录、team.json、
   registry、tasks、lead 邮箱、current.json。
3. 实现 `load_current()`：加载团队 → 重建注册表（如需）→ 非 terminated 成员置 offline。
4. 名称非法、重复创建、未知团队、当前组损坏 → 明确错误/忽略并提示。
5. 测试：创建两个组、切换、持久化重载、离线标记、非法名/重复创建。

**验证：** `python -m pytest tests/teams/test_manager.py -q`

## T16：成员派生、终止与关闭

**文件：** `codepilot/teams/manager.py`、`tests/teams/test_manager.py`
**依赖：** T11、T15、T9

**步骤：**
1. `spawn_member`：校验角色/名称/后端/审批标志 → spawning → worktree enter（to_thread）
   → 构造 Agent/运行时 → start → idle。
2. 失败回滚：元数据/注册表/本次新建的 worktree 与分支，不残留半成品。
3. `terminate_member`：runtime.stop → `worktree.release` → terminated；目录/分支保留。
4. offline 同名成员可重新派生（新上下文）；terminated 拒绝；运行中同名拒绝。
5. `shutdown()`：取消全部运行时、释放所有 worktree 占用锁。
6. 测试：派生成功、非法后端、失败回滚、终止保目录、offline 重新派生、重复名拒绝。

**验证：** `python -m pytest tests/teams/test_manager.py -q`

## T17：消息门面、事件与任务门面

**文件：** `codepilot/teams/manager.py`、`tests/teams/test_manager.py`
**依赖：** T8、T13、T15

**步骤：**
1. `send`：解析注册表 → 邮箱追加 → 唤醒目标成员；发给 Lead 时触发 `TeamEvent`。
2. `broadcast`：解析所有非 terminated 成员 + Lead，排除发送者，返回成功/失败列表。
3. `set_member_status`：更新元数据/注册表并发送 `status` 消息；同状态去重。
4. `pending_approvals`：成员发 `plan_request` 时记录 task_id→member；decision 后清除。
5. 任务门面：创建/更新任务后，若 assignee 变化则唤醒对应成员。
6. `set_on_event`：事件回调只做提示；测试收集事件。
7. 测试：点对点、广播、未知/terminated 拒绝、事件回调、审批映射、指派唤醒。

**验证：** `python -m pytest tests/teams/test_manager.py -q`

## T18：Lead 编排工具

**文件：** `codepilot/teams/tools_lead.py`、`tests/teams/test_tools_lead.py`
**依赖：** T15、T16、T17

**步骤：**
1. 实现 13 个工具（建组/列表/切换/展示/派生/终止/发送/广播/决定/收件箱/
   建任务/改任务/列任务）。
2. 全部 `system_exempt=True`；写操作 `side_effect=True`；异常转 `ToolResult`。
3. `team_decide` 只接受存在 pending approval 的任务；`team_inbox` 消费 Lead 未读。
4. 参数 schema、描述与使用提示完整；无当前组时明确报错。
5. 测试：每个工具的成功/失败路径、决定映射、收件箱消费、无组错误。

**验证：** `python -m pytest tests/teams/test_tools_lead.py -q`

## T19：工具面隔离

**文件：** `codepilot/teams/__init__.py`（或工具模块常量）、`tests/teams/test_visibility.py`
**依赖：** T14、T18

**步骤：**
1. 导出 `LEAD_TOOL_NAMES`、`MEMBER_TOOL_NAMES`、`ALL_TEAM_TOOL_NAMES` 常量。
2. 提供构造 base/lead registry 的辅助函数（或由 main 直接构造）并确保：
   base 无任何团队工具；lead 只有 Lead 工具；成员 registry 只有成员工具。
3. Fork 父工具集过滤：从 `parent_visible` 去掉 `ALL_TEAM_TOOL_NAMES`。
4. 测试：三套 registry 的工具名断言；普通子 Agent 共享 base；成员 filter 不泄漏。

**验证：** `python -m pytest tests/teams/test_visibility.py -q`

## T20：main 装配

**文件：** `codepilot/main.py`、`tests/teams/test_wiring.py`
**依赖：** T18、T19

**步骤：**
1. 启动读取 `TeamSettings`；非法配置打印错误并退出。
2. 构造 `base_registry`；TeamManager 注入全部依赖；`lead_registry = base.clone()` 后
   注册 Lead 工具。
3. 主 Agent 使用 `lead_registry`；SubAgentManager 使用 `base_registry`。
4. 主 Agent 注入块合并「角色名册 + Lead 团队上下文」；`parent_visible` 排除团队工具。
5. `_run_ui` 退出时 `await team_manager.shutdown()`；App 注入 team_manager。
6. 测试：导入成功、registry 分流、当前组加载不崩溃、无组时启动正常。

**验证：** `python -m pytest tests/teams/test_wiring.py -q && python -c "import codepilot.main; print('ok')"`

## T21：App 事件回灌与命令上下文

**文件：** `codepilot/ui/app.py`、`tests/test_team_app.py`
**依赖：** T17、T20

**步骤：**
1. App 构造参数增加 `team_manager`；on_mount 注册 `set_on_event`。
2. 实现 `_on_team_event`：聊天区 dim 提示；流式中放入 `_pending_team`，
   流式结束后合并进主对话；非流式立即合并。
3. 复用 state17 的安全边界：不把通知插进 tool_call/tool_result 之间；内容 escaped。
4. `_CommandContextImpl.team_manager` 属性。
5. 测试（headless）：空闲时立即回灌、流式中先入队、结束后回灌、Rich 标记不注入。

**验证：** `python -m pytest tests/test_team_app.py -q`

## T22：`/team` 命令

**文件：** `codepilot/commands/builtins.py`、`tests/test_commands_builtins.py`
**依赖：** T15、T20、T21

**步骤：**
1. 注册 LOCAL 命令 `team`，支持 `list`、`use <name>`、`show [name]`、`create <name>`。
2. 输出：当前组标记、成员状态、任务计数、worktree 路径/分支；无组时提示建组。
3. manager 缺失时明确报“团队系统未启用”。
4. 更新内置命令全集断言；测试成功/失败/降级路径。

**验证：** `python -m pytest tests/test_commands_builtins.py -q`

## T23：端到端与可见性测试

**文件：** `tests/teams/test_end_to_end.py`、`tests/teams/test_visibility.py`、`tests/teams/conftest.py`
**依赖：** T18–T22

**步骤：**
1. conftest 提供临时 team root、临时 Git 仓库、假 provider/假 agent 工厂。
2. 端到端：建组 → 建两个带依赖任务 → 派生两个成员 → 指派/领取 → 一次审批 →
   完成 → 检查任务板、邮箱、成员状态、worktree 路径/分支、Lead 报告数据。
3. 断言不执行 merge、dirty worktree 保留、失败成员不阻塞其他成员。
4. 可见性：Lead/成员/普通子 Agent 三套工具面；Fork 不继承团队工具。
5. 损坏记录隔离、审批超时、锁失败等异常路径补充集成覆盖。

**验证：** `python -m pytest tests/teams/test_end_to_end.py tests/teams/test_visibility.py -q`

## T24：配置示例、回归与架构检查

**文件：** `config.example.yaml`、`docs/state19-team/`（如需记录实现偏差）
**依赖：** 全部

**步骤：**
1. `config.example.yaml` 增加 `team:` 段与默认值注释。
2. 全量测试：`python -m pytest -q`，数量不少于基线 730。
3. `ruff check .` 仍为 27 条既有告警，不新增。
4. 检查无 `os.chdir`；`codepilot/teams/` 领域模块不 import UI/Textual；
   成员 registry 无 `agent`/`load_skill`；三套工具面断言通过。
5. 如有偏离 plan 的实现裁决，记录到 plan/task 或 `docs/decisions.md`。

**验证：**
```bash
python -m pytest -q
ruff check .
python -c "import codepilot.main, codepilot.teams; print('ok')"
```

## 执行顺序

```text
T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8
                    ↘ T9（独立）
T1 → T10 → T11 → T12 → T13
                 ↘ T14
T5/T6/T7 → T15 → T16 → T17 → T18
T14/T18 → T19 → T20 → T21 → T22
T13/T17/T18/T19 → T23 → T24
```

- T9 可在 T1–T8 期间并行。
- T14 可在 T12 完成后与 T13 并行。
- T15 可与 T11–T13 并行（只依赖领域层）。
- T19 是两条线汇合点；T20–T24 串行。

## 提交建议（中文 Conventional Commits）

- `feat(state19): 团队领域模型与持久化基础（T1–T9）`
- `feat(state19): 成员运行时与审批闭环（T10–T14）`
- `feat(state19): TeamManager 与 Lead/成员工具面（T15–T19）`
- `feat(state19): 主流程装配、事件回灌与 /team 命令（T20–T22）`
- `test(state19): 端到端与工具面隔离覆盖（T23）`
- `chore(state19): 配置示例与回归收口（T24）`
- `docs(state19): 四文档与验收记录`（验收后）
