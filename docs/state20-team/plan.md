# Team Lead 强隔离与收口 Plan

## 架构概览

在 state19 基础上新增三个能力域，依赖单向：

1. **A2 成员上下文跨进程恢复**（`teams/context.py` + 扩展 storage/runtime/manager）
   - 复用 state12 的 `session/store.py` + `session/restore.py`：把成员协议原生 `_msgs`
     转成会话 JSONL 扁平行落盘，恢复时用 `restore_chat` 重建协议原生序列。
   - 恢复钩子在 `spawn_member`（复用派生自动恢复）；落盘钩子在 `_run_agent` 结束。

2. **A3 git 合并/冲突/回滚**（`teams/merge.py` + 扩展 `worktree/git.py`）
   - `TeamMerger`：把成员 worktree 未提交改动 commit 到成员分支，再合并到固定汇聚分支
     `codepilot/team/<team>/merged`；冲突则整体回滚并上报文件清单。
   - 扩展 `GitRunner` 补 add/commit/checkout/merge/abort/diff。

3. **A4 coordinator 双锁**（`teams/coordinator.py` + settings/prompts/main/tools_lead）
   - `coordinator_enabled`：配置开关 ∧ 环境变量双开才生效。
   - 生效时 lead registry 移除 write/edit，保留 read/run/team_*；注入 coordinator 提示块。

依赖方向：
```
teams/{context,merge,coordinator} ──→ worktree/git.py（扩展 GitRunner）
    │
    └──────────────────────────→ session/store.py + restore.py（复用，不改）
main.py ──→ coordinator 检测 + lead registry 收窄 + 注入
```

## 核心数据结构

```python
# —— A2 成员上下文 ——
# 复用 state12 会话 JSONL 扁平行格式；路径 <team_root>/<team>/context/<member>.jsonl

# —— A3 合并 ——
@dataclass(frozen=True)
class MergeResult:
    ok: bool
    target: str                         # 汇聚分支 codepilot/team/<team>/merged
    merged_members: tuple[str, ...]     # 实际合入的成员
    committed_members: tuple[str, ...]  # 本次产生了新提交的成员
    conflict_files: tuple[str, ...]     # 冲突文件清单（ok=False 时）
    error: str = ""

@dataclass(frozen=True)
class MemberChanges:
    member: str
    branch: str
    dirty: bool          # worktree 是否有未提交改动
    ahead: int           # 相对基线的提交数
    committed: bool      # 本次是否产生了新提交

# —— A4 coordinator ——
@dataclass(frozen=True)
class CoordinatorState:
    enabled: bool
    source: str             # "config+env" | "config" | "env" | ""
    write_tools_removed: tuple[str, ...] = ()
```

## 核心接口

```python
# —— A2 ——
def save_member_msgs(storage, team, member, msgs) -> None
    # 增量/全量：协议原生 msgs → 会话 JSONL 扁平行（append_message/append_assistant_toolcalls/append_tool_result）
def load_member_msgs(storage, team, member, *, protocol) -> list[dict] | None
    # restore_chat(context_path, protocol)["messages"]；无文件/损坏/异常 → None

# —— A3 ——
class TeamMerger:
    def __init__(self, workspace, git, storage, registry): ...
    def commit_member_changes(self, member: MemberRef) -> MemberChanges
    def merge_members(self, team: str, members: list[MemberRef]) -> MergeResult

# —— A4 ——
def coordinator_enabled(settings, env=None) -> CoordinatorState
def coordinator_tool_filter(tools, state) -> list
def coordinator_context_block(state) -> str
```

## 模块设计

### `codepilot/worktree/git.py`（扩展 GitRunner）

**新增方法**（全部参数列表 + 显式 cwd）：

```python
def add(self, path: Path, *paths: str) -> None
def commit(self, path: Path, message: str) -> None
def checkout_branch(self, path: Path, branch: str) -> None   # -b 创建并切换
def merge(self, path: Path, branch: str) -> bool             # 返回是否有冲突
def abort_merge(self, path: Path) -> None
def diff_names(self, path: Path, base: str) -> tuple[str, ...]
def branch_created(self, branch: str) -> bool
```

**依赖：** 标准库。

### `codepilot/teams/context.py`（新）

**职责：** A2 成员上下文持久化与恢复。

- `save_member_msgs`：把协议原生 msgs 转扁平行。
  - assistant 含 tool_use 块 → `append_assistant_toolcalls(content, 扁平 tool_calls)`
  - user 含 tool_result 块 → `append_tool_result(call_id/name/output/error)`
  - 纯文本 user/assistant → `append_message(role/content)`
  - 幂等：只写新增段（游标），重复调用不重复写。
- `load_member_msgs`：`restore_chat(path, protocol=...)["messages"]`；
  无文件/损坏/恢复异常 → None。
- `member_context_path(storage, team, member)`：`<team_root>/<team>/context/<member>.jsonl`。

**依赖：** `session/store.py`、`session/restore.py`、`teams/storage.py`、`teams/naming.py`。

### `codepilot/teams/storage.py`（扩展）

- `ensure_team_layout` 增加 `context/` 子目录。
- `member_context_path(team, member)`：返回 context 文件路径（复用 `team_dir` 校验）。

### `codepilot/teams/merge.py`（新）

**职责：** A3 合并编排。

- `TeamMerger.__init__(workspace, git, storage, registry)`。
- `commit_member_changes(member)`：
  - 在成员 worktree 执行 `status_porcelain` → dirty；
  - dirty → `git add .` + `git commit`（msg：`team <member>: <ts> 成员改动`）；
  - 返回 `MemberChanges`；提交失败 → 记录异常成员，不中断整体。
- `merge_members(team, members)`：
  1. 逐个 commit_member_changes；
  2. 确保汇聚分支 `codepilot/team/<team>/merged`（不存在 → `checkout_branch` 创建，基于当前 HEAD；已存在 → 检出）；
  3. 逐个 `git merge <member-branch>`；冲突 → `abort_merge` + `diff_names` → `MergeResult(ok=False, conflict_files=...)`；
  4. 全合完 → `MergeResult(ok=True, ...)`。

**依赖：** `worktree/git.py`、`teams/storage.py`、`teams/registry.py`、`teams/model.py`。

### `codepilot/teams/coordinator.py`（新）

**职责：** A4 双锁检测、工具面收窄、提示块。

```python
def coordinator_enabled(settings, env=None) -> CoordinatorState:
    cfg_on = settings.coordinator_enabled
    env_on = (env or os.environ).get("CODEPILOT_COORDINATOR", "0") in {"1","true","on"}
    enabled = cfg_on and env_on
    # source 记录实际组合

def coordinator_tool_filter(tools, state):
    # state.enabled 时移除 write/edit；返回过滤后的工具列表（不改原对象）

def coordinator_context_block(state) -> str:
    # "你是纯编排者：文件修改交给成员；用 run 执行 git 合并；只做派人/终止/审计划/发消息/合并"
```

**依赖：** `teams/settings.py`、`teams/model.py`。

### `codepilot/teams/settings.py`（扩展）

- `TeamSettings` 增加 `coordinator_enabled: bool = False`。
- `load_team_settings` 读 `team.coordinator_enabled`（bool 校验，缺省 False）。

### `codepilot/teams/manager.py`（扩展）

- `spawn_member`：构造 Agent 前调用 `context.load_member_msgs(...)`；
  有 → 传给 runtime 作为 `_msgs` 初始值；无/损坏 → 空（state19 语义）。
- `merge_team(members=None)` 门面：取 registry 非 terminated 成员 → `asyncio.to_thread(TeamMerger.merge_members)`。
- `lead_context()`：coordinator enabled 时追加 `coordinator_context_block`。

### `codepilot/teams/runtime.py`（扩展）

- `MemberRuntime` 持有 context 落盘游标（`_persisted_msgs_len`）。
- `_run_agent` 结束后调用 `save_member_msgs`（增量追加新段）。

### `codepilot/teams/tools_lead.py`（扩展）

- 新增 `TeamMergeTool`（`side_effect=True`、`system_exempt=True`）：
  - 参数：`members`（可选，缺省当前组所有非 terminated）；
  - 调 `manager.merge_team` → MergeResult 文本化返回。

### `codepilot/main.py`（扩展）

- 装配：`coordinator_state = coordinator_enabled(team_settings)`；
- 构造 lead registry 时若 enabled，对 `build_lead_tools(manager)` 过滤写工具；
- `_subagent_blocks` 追加 coordinator 块（有 state 时）。

## 模块交互

### 恢复路径
```
main 启动 → load_current → 成员 offline
Lead team_spawn(bob) → spawn_member
  → context.load_member_msgs(storage, team, "bob", protocol)
  → 有 → runtime._msgs = 历史；无/损坏 → 空（state19 一致）
```

### 落盘路径
```
MemberRuntime._run_agent 结束 → context.save_member_msgs(增量游标)
```

### 合并路径
```
Lead team_merge → manager.merge_team
  → asyncio.to_thread(TeamMerger.merge_members)
    → commit 成员改动 → checkout 汇聚分支 → merge → abort/diff（冲突）
  → MergeResult → tool 输出
```

### coordinator
```
main → coordinator_enabled(settings, env) → state
  → lead registry 过滤写工具 + _subagent_blocks 追加 coordinator 块
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| A2 持久化格式 | 复用 state12 会话 JSONL（协议原生→扁平转换，恢复用 restore_chat） | 已测试往返；自带 dangling 截断；与主会话一致 |
| A2 增量 | 游标记录已持久化长度，每轮追加 | 崩溃前尽量完整；幂等 |
| A2 恢复钩子 | spawn_member 内、构造 Agent 前 | 不新增入口，符合"复用派生自动恢复" |
| A3 Git 调用 | 扩展 GitRunner（add/commit/checkout/merge/abort/diff/branch_created） | 参数列表+显式 cwd，统一 Git 出口 |
| A3 目标 | 固定汇聚分支 `codepilot/team/<team>/merged` | 不污染当前分支；命名符合 state18 |
| A3 冲突 | abort + 冲突文件清单上报 | 安全确定性；不做 LLM 裁决 |
| A3 提交 | 合并前把成员 dirty 改动 commit 到成员分支 | 成员从不 commit（state19），合并必须落到分支 |
| A4 双锁 | config.team.coordinator_enabled ∧ env CODEPILOT_COORDINATOR | 两把锁语义明确；缺一即 state19 |
| A4 收窄名单 | 移除 write/edit，保留 run/read/grep/glob/team_* | run 要跑 git 合并；成员不受影响 |
| A4 注入 | coordinator_context_block 追加到 lead_context volatile | 复用现有注入缝 |

## 文件组织

```text
codepilot/teams/
├── context.py            (新) A2
├── merge.py              (新) A3
├── coordinator.py        (新) A4
├── settings.py           (改) coordinator_enabled
├── storage.py            (改) context/ 子目录 + member_context_path
├── manager.py            (改) spawn 恢复钩子 + merge_team + coordinator 块
├── runtime.py            (改) 落盘钩子
├── tools_lead.py         (改) TeamMergeTool
└── __init__.py           (改) 导出

修改：
├── codepilot/worktree/git.py
├── codepilot/main.py
├── config.example.yaml

测试：
├── tests/teams/test_context.py      (新)
├── tests/teams/test_merge.py        (新)
├── tests/teams/test_coordinator.py  (新)
├── tests/worktree/test_git.py       (改)
├── tests/teams/test_manager.py      (改)
├── tests/teams/test_runtime.py      (改)
├── tests/teams/test_tools_lead.py   (改)
├── tests/teams/test_settings.py     (改)
├── tests/teams/test_wiring.py       (改)
```

## 执行顺序

```text
T1  GitRunner 扩展 —— 独立，最先
T2  A2 context.py + storage + 单测
T3  A2 钩子（runtime 落盘 + manager 恢复）+ 单测
T4  A3 merge.py + TeamMergeTool + manager.merge_team + 临时仓库单测（可与 A2 并行）
T5  A4 settings + coordinator.py + 四组合单测
T6  A4 装配（main 收窄 + 注入）+ test_wiring
T7  端到端组合场景
T8  全量回归 + lint + config.example
```

## 风险与已知边界

1. **上下文往返失真**：协议原生 msgs → 扁平 → restore 重建理论上无损；用"保存后立即恢复 == 原 msgs"往返测试锁住。
2. **增量游标与并发**：成员 `_run_agent` 串行单协程，无并发问题；进程崩溃可能缺最后一轮（"尽量完整"非"保证全量"）。
3. **merge 同步阻塞**：大仓库耗时；调用方用 `to_thread` 卸；期间 worktree 占用锁保持。
4. **merge 冲突整体回滚**：只回到本次 merge 前状态（`merge --abort` 语义），不丢已合内容。
5. **coordinator 收窄名单**：只列 write/edit；新增写工具需同步。run 保留（git 合并需要）——用户可借此变通写文件，接受（与 state19 一致）。
6. **coordinator 提示层为主**：工具面收窄是机制（真移除），提示块是软引导；run 保留故不堵变通写文件，接受边界。

## Spec 覆盖对照

| Spec | Plan 落点 |
|------|-----------|
| F1 跨进程持久化 | context.py save_member_msgs |
| F2 派生恢复 | context.py load_member_msgs + spawn_member 钩子 |
| F3 合并前落盘 | merge.py commit_member_changes + git.py add/commit |
| F4 合并汇聚分支 | merge.py merge_members + git.py checkout/merge |
| F5 冲突回滚 | merge.py abort + git.py abort_merge/diff_names |
| F6 双锁 | coordinator.py coordinator_enabled + settings.py |
| F7 工具面收窄 | coordinator_tool_filter + main.py 装配 |
| F8 合并工具 | tools_lead.py TeamMergeTool + manager.merge_team |
| N1/N3 | merge 只在汇聚分支、主工作区不污染 |
| N2 | 各步骤结构化日志 |
| N4/N5 | 纯函数 + 临时 Git 仓库测试 + 跨平台 |
