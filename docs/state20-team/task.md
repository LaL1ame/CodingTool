# Team Lead 强隔离与收口 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 修改 | `codepilot/worktree/git.py` | 扩展 add/commit/checkout/merge/abort/diff/branch_created |
| 新建 | `codepilot/teams/context.py` | A2 成员上下文落盘/恢复 |
| 修改 | `codepilot/teams/storage.py` | context/ 子目录 + member_context_path |
| 修改 | `codepilot/teams/manager.py` | spawn 恢复钩子 + merge_team + coordinator 块 |
| 修改 | `codepilot/teams/runtime.py` | 落盘钩子 |
| 新建 | `codepilot/teams/merge.py` | A3 TeamMerger |
| 新建 | `codepilot/teams/coordinator.py` | A4 三函数 |
| 修改 | `codepilot/teams/settings.py` | coordinator_enabled |
| 修改 | `codepilot/teams/tools_lead.py` | TeamMergeTool |
| 修改 | `codepilot/main.py` | coordinator 装配 + 收窄 + 注入 |
| 修改 | `codepilot/teams/__init__.py` | 导出 |
| 修改 | `config.example.yaml` | team.coordinator_enabled |
| 新建 | `tests/teams/test_context.py` | A2 |
| 新建 | `tests/teams/test_merge.py` | A3 |
| 新建 | `tests/teams/test_coordinator.py` | A4 |
| 修改 | `tests/worktree/test_git.py` | GitRunner 新方法 |
| 修改 | `tests/teams/test_manager.py` | 恢复 + merge |
| 修改 | `tests/teams/test_runtime.py` | 落盘钩子 |
| 修改 | `tests/teams/test_tools_lead.py` | team_merge |
| 修改 | `tests/teams/test_settings.py` | coordinator |
| 修改 | `tests/teams/test_wiring.py` | 装配 |

## T1：GitRunner 扩展

**文件：** `codepilot/worktree/git.py`、`tests/worktree/test_git.py`
**依赖：** 无

**步骤：**
1. 在 `GitRunner` 增加：`add`、`commit`、`checkout_branch`（`-b` 创建并切换）、`merge`（返回 bool 是否有冲突）、`abort_merge`、`diff_names`、`branch_created`。
2. 全部走 `self.run(args, cwd=...)`，参数列表、无 shell 拼接。
3. `merge` 冲突检测：`git merge` 退出码非 0 视为冲突。

**验证：** `python -m pytest tests/worktree/test_git.py -q`

## T2：A2 context.py + storage

**文件：** `codepilot/teams/context.py`、`codepilot/teams/storage.py`、`tests/teams/test_context.py`
**依赖：** T1

**步骤：**
1. `storage.ensure_team_layout` 加 `context/`；`member_context_path(team, member)`。
2. `context.save_member_msgs`：协议原生 msgs → 会话 JSONL 扁平行（复用 `store.append_*`）；幂等增量（游标）。
3. `context.load_member_msgs`：`restore_chat(path, protocol)["messages"]`；无/损坏/异常 → None。
4. 往返测试：构造协议原生 msgs（assistant 带 tool_use、user 带 tool_result、纯文本），保存→恢复 == 原 msgs。

**验证：** `python -m pytest tests/teams/test_context.py -q`

## T3：A2 钩子（runtime 落盘 + manager 恢复）

**文件：** `codepilot/teams/runtime.py`、`codepilot/teams/manager.py`、`tests/teams/test_runtime.py`、`tests/teams/test_manager.py`
**依赖：** T2

**步骤：**
1. `MemberRuntime` 加 `_persisted_msgs_len` 游标；`_run_agent` 结束后调 `save_member_msgs`（增量）。
2. `spawn_member`：构造 Agent 前 `load_member_msgs`；有 → 作为 `_msgs` 初始值；无/损坏 → 空。
3. 测试：成员运行后 context 文件生成；重建 runtime 用历史；损坏文件降级全新不崩。

**验证：** `python -m pytest tests/teams/test_runtime.py tests/teams/test_manager.py -q`

## T4：A3 TeamMerger + 合并工具

**文件：** `codepilot/teams/merge.py`、`codepilot/teams/tools_lead.py`、`codepilot/teams/manager.py`、`tests/teams/test_merge.py`、`tests/teams/test_tools_lead.py`、`tests/teams/test_manager.py`
**依赖：** T1

**步骤：**
1. `TeamMerger.commit_member_changes`：worktree dirty → `git add .` + `git commit`（msg `team <member>: <ts> 成员改动`）；返回 `MemberChanges`；提交失败记异常成员不中断。
2. `TeamMerger.merge_members`：确保汇聚分支 → 逐个 merge → 冲突 abort + diff → `MergeResult`。
3. `TeamManager.merge_team(members=None)` 门面：取非 terminated 成员 → `asyncio.to_thread`。
4. `tools_lead.TeamMergeTool`：`side_effect=True`、`system_exempt=True`，缺省合当前组。
5. 临时 Git 仓库测试：无冲突合并（汇聚分支有成员改动、当前分支不变）、冲突回滚（成员分支/主工作区原样、报告冲突文件）、提交信息、前提校验失败。

**验证：** `python -m pytest tests/teams/test_merge.py tests/teams/test_tools_lead.py tests/teams/test_manager.py -q`

## T5：A4 coordinator.py + settings

**文件：** `codepilot/teams/coordinator.py`、`codepilot/teams/settings.py`、`tests/teams/test_coordinator.py`、`tests/teams/test_settings.py`
**依赖：** T2

**步骤：**
1. `TeamSettings.coordinator_enabled: bool = False`；`load_team_settings` 读 `team.coordinator_enabled`。
2. `coordinator_enabled(settings, env=None)`：cfg ∧ env 双开 → enabled；source 记录。
3. `coordinator_tool_filter(tools, state)`：enabled 时移除 write/edit。
4. `coordinator_context_block(state)`：编排者说明文本。
5. 测试：四组合（开开/开关/关开/关关）仅开开 enabled；source 正确；收窄只移除 write/edit 保留 run/read/team_*；块文本含关键约束。

**验证：** `python -m pytest tests/teams/test_coordinator.py tests/teams/test_settings.py -q`

## T6：A4 装配

**文件：** `codepilot/main.py`、`tests/teams/test_wiring.py`
**依赖：** T5

**步骤：**
1. main：`coordinator_state = coordinator_enabled(team_settings)`。
2. 构造 lead registry：enabled 时对 `build_lead_tools(manager)` 过滤写工具。
3. `_subagent_blocks` 追加 `coordinator_context_block`（有 state 且 enabled 时）。
4. 测试：无 coordinator 时 lead registry 完整；开开时 write/edit 不在、run/team_* 在；注入块出现；`python -c "import codepilot.main"` 通过。

**验证：** `python -m pytest tests/teams/test_wiring.py -q && python -c "import codepilot.main; print('ok')"`

## T7：端到端组合

**文件：** `tests/teams/test_context.py`、`tests/teams/test_merge.py`、`tests/teams/test_coordinator.py`、`tests/teams/conftest.py`
**依赖：** T4、T6

**步骤：**
1. conftest 提供临时 Git 仓库 + 临时 team root。
2. 场景 A：派生成员 → 运行产生对话 → 模拟重启（重建 manager）→ 重新派生 → 恢复历史。
3. 场景 B：两个成员各自改文件 → `team_merge` → 无冲突合到汇聚分支、主工作区不变。
4. 场景 C：构造冲突 → 回滚 + 冲突文件清单。
5. 场景 D：coordinator 开开 → Lead 工具面无 write/edit；关 → 完整。

**验证：** `python -m pytest tests/teams/test_context.py tests/teams/test_merge.py tests/teams/test_coordinator.py -q`

## T8：全量回归 + lint + config

**文件：** `config.example.yaml`、`codepilot/teams/__init__.py`
**依赖：** 全部

**步骤：**
1. `__init__.py` 导出 context/merge/coordinator 相关。
2. `config.example.yaml` 加 `team.coordinator_enabled: false` 注释。
3. 全量测试 ≥918；`ruff check .` 27 基线不新增。
4. 无 `os.chdir`；Git 用参数列表。

**验证：**
```bash
python -m pytest -q
ruff check .
python -c "import codepilot.teams.context, codepilot.teams.merge, codepilot.teams.coordinator; print('ok')"
```

## 执行顺序

```text
T1 → T2 → T3（A2 串行）
T1 → T4（A3，与 A2 并行）
T2 → T5 → T6（A4，与 A2/A3 并行）
T4/T6 → T7 → T8
```

## 提交建议（中文 Conventional Commits）

- `feat(state20): GitRunner 补合并命令（T1）`
- `feat(state20): 成员上下文跨进程恢复（T2–T3）`
- `feat(state20): TeamMerger 合并/冲突回滚 + team_merge（T4）`
- `feat(state20): coordinator 双锁与工具面收窄（T5–T6）`
- `test(state20): 端到端与回归（T7–T8）`
- `docs(state20): 四文档与验收记录`（验收后）
