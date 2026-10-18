# Team Lead 长期小组 Plan

## 架构概览

本阶段分为四层，依赖单向：

1. **团队领域与存储层**（新包 `codepilot/teams/`）
   - `model / naming / settings / locks / storage / tasks / registry / mailbox`
   - 只负责小组元数据、任务板、名称注册表、邮箱、锁、原子读写。
   - 不 import Agent/UI/Textual，不感知成员如何运行。

2. **成员运行时层**
   - `runtime.py / prompts.py / tools_member.py`
   - 复用现有 `Agent` 类，构造长期存活的成员实例；驱动任务循环、计划/审批等待、
     idle/唤醒、状态上报。
   - 依赖团队领域层、state17 角色模型、state18 worktree 句柄与隔离运行时装配；
     不 import UI。

3. **Lead 编排层**
   - `manager.py / tools_lead.py`
   - `TeamManager` 管理当前小组、派生/终止成员、任务与消息门面、Lead 事件回调。
   - Lead 工具只注册到主 Agent 的独立 registry。

4. **装配与界面层**
   - `main.py`：配置加载、base/lead 两套 registry、TeamManager 与 Lead 工具装配。
   - `ui/app.py`：注入 `team_manager`、接收 Lead 事件并安全回灌主对话。
   - `commands/builtins.py`：`/team` 命令。
   - `worktree/manager.py` 增加 `release(handle)`（保留目录、仅释放占用锁）。
   - `tools/registry.py` 增加 `clone()`（构造 Lead registry，不动现有工具实例）。
   - `teams/settings.py`：新增 team 配置读取。

依赖方向：

```text
main / ui / commands
        │
        ▼
tools_lead → TeamManager → TaskBoard / MemberRegistry / Mailbox / storage / locks
        │                     │
        │                     └─ MemberRuntime → Agent / worktree / permissions / context
        ▼
tools_member（只进入成员 Agent 的 registry）
```

团队领域层不依赖运行时层；运行时层不依赖 Lead 工具与 UI。

## 核心数据结构

```python
class TeamMemberStatus(StrEnum):
    SPAWNING = "spawning"
    RUNNING = "running"
    IDLE = "idle"
    BLOCKED = "blocked"
    OFFLINE = "offline"
    TERMINATED = "terminated"
    FAILED = "failed"

class TeamMemberBackend(StrEnum):
    INPROCESS = "inprocess"
    TERMINAL = "terminal"      # 预留，state19 不实现

class TaskStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    CANCELLED = "cancelled"

class TeamMessageType(StrEnum):
    TEXT = "text"
    BROADCAST = "broadcast"
    PLAN_REQUEST = "plan_request"
    PLAN_DECISION = "plan_decision"
    STATUS = "status"
```

> 实现注：项目声明 `requires-python >=3.10`，而 `StrEnum` 是 3.11+；代码中统一用
> `class X(str, Enum)`，语义等价。

```python
@dataclass(frozen=True)
class TeamSettings:
    root: Path                         # 默认 ~/.config/codepilot/teams
    approval_timeout_seconds: float    # 默认 600
    approval_poll_seconds: float       # 默认 0.5
    lock_timeout_seconds: float        # 默认 5
    lock_stale_seconds: float          # 默认 30
    lock_retry_seconds: float          # 默认 0.05
    message_summary_chars: int         # 默认 120
    max_unread_per_wakeup: int         # 默认 20
    default_backend: str               # 默认 "auto"

@dataclass(frozen=True)
class TeamMeta:
    name: str
    lead: str                          # 固定 "lead"
    members: tuple[str, ...]           # 花名册（名字）
    created_at: float
    next_member_seq: int               # 派生 worktree 用的稳定序号

@dataclass(frozen=True)
class TeamMember:
    name: str
    role: str
    worktree_path: Path
    branch: str
    backend: TeamMemberBackend
    requires_approval: bool
    status: TeamMemberStatus
    created_at: float
    updated_at: float
    note: str = ""                     # 失败/blocked 原因

@dataclass(frozen=True)
class MemberRef:
    name: str
    role: str
    status: TeamMemberStatus
    backend: TeamMemberBackend
    mailbox_path: Path
    worktree_path: Path
    branch: str
    requires_approval: bool

@dataclass(frozen=True)
class Task:
    id: int
    title: str
    description: str
    status: TaskStatus
    assignee: str | None
    depends_on: tuple[int, ...]
    created_at: float
    updated_at: float
    result: str = ""

@dataclass(frozen=True)
class TeamMessage:
    id: str
    sender: str
    recipient: str
    type: TeamMessageType
    body: str
    summary: str
    timestamp: float
    read: bool
    task_id: int | None = None
    approved: bool | None = None
    reason: str = ""
    status: str = ""

@dataclass(frozen=True)
class TeamEvent:
    kind: str                  # "message" | "status" | "spawned" | "terminated" | "failed"
    member: str
    task_id: int | None
    summary: str
    mailbox_path: Path | None
```

## 核心接口

```python
class FileLock:
    def __init__(self, path, *, timeout, stale_after, retry,
                 clock=time.time, sleep=time.sleep): ...
    def __enter__(self) -> "FileLock": ...
    def __exit__(self, *exc) -> None: ...

class TeamStorage:
    def __init__(self, root: Path, settings: TeamSettings): ...
    def team_dir(self, team: str) -> Path: ...
    def atomic_write_json(self, path: Path, data: object) -> None: ...
    def read_json(self, path: Path, default=None) -> object: ...
    def read_jsonl(self, path: Path) -> list[dict]: ...
    def rewrite_jsonl(self, path: Path, rows: list[dict]) -> None: ...

class TaskBoard:
    def create(self, *, title, description, depends_on, assignee=None) -> Task: ...
    def list(self, *, status=None, assignee=None) -> list[Task]: ...
    def get(self, task_id: int) -> Task: ...
    def update(self, task_id: int, **fields) -> Task: ...
    def delete(self, task_id: int) -> None: ...
    def claim(self, task_id: int, member: str) -> Task: ...
    def deps_satisfied(self, task: Task) -> bool: ...

class MemberRegistry:
    def resolve(self, name: str) -> MemberRef: ...
    def resolve_active(self, name: str) -> MemberRef: ...
    def list(self) -> list[MemberRef]: ...
    def update(self, name: str, **fields) -> MemberRef: ...
    def rebuild(self) -> None: ...

class Mailbox:
    def __init__(self, path: Path, *, settings: TeamSettings,
                 lock: FileLock | None = None, clock=time.time): ...
    def append(self, message: TeamMessage) -> TeamMessage: ...
    def consume_unread(self, *, limit: int) -> list[TeamMessage]: ...
    def consume_matching(self, *, type, task_id,
                         limit: int = 1) -> list[TeamMessage]: ...
    def peek(self) -> list[TeamMessage]: ...

class TeamManager:
    def create_team(self, name: str) -> TeamMeta: ...
    def list_teams(self) -> list[str]: ...
    def use_team(self, name: str) -> TeamMeta: ...
    def current_team(self) -> TeamMeta | None: ...
    async def spawn_member(self, *, name, role, backend="auto",
                           requires_approval=False) -> TeamMember: ...
    async def terminate_member(self, name: str) -> TeamMember: ...
    def send(self, *, sender, recipient, type, body, **payload) -> TeamMessage: ...
    def broadcast(self, *, sender, body) -> list[TeamMessage]: ...
    def tasks(self) -> TaskBoard: ...
    def registry(self) -> MemberRegistry: ...
    def set_on_event(self, cb: Callable[[TeamEvent], None]) -> None: ...
    async def shutdown(self) -> None: ...

class MemberRuntime:
    def __init__(self, *, member, team, agent, mailbox, board, manager, settings): ...
    async def start(self) -> None: ...
    async def stop(self, *, reason: str) -> None: ...
    def wake(self) -> None: ...
    @property
    def status(self) -> TeamMemberStatus: ...
```

## 模块设计

### `codepilot/teams/model.py`

**职责：** 枚举、数据类、错误类型；无 IO。

**对外接口：**

```python
class TeamError(Exception): ...
class TeamSettingsError(TeamError): ...
class TeamNameError(TeamError): ...
class TeamLockError(TeamError): ...
class TeamTaskError(TeamError): ...
class TeamMessageError(TeamError): ...
class TeamMemberError(TeamError): ...
class TeamBackendError(TeamError): ...

TeamMemberStatus / TeamMemberBackend / TaskStatus / TeamMessageType
TeamSettings / TeamMeta / TeamMember / MemberRef / Task / TeamMessage / TeamEvent
```

**依赖：** 仅标准库。

### `codepilot/teams/naming.py`

**职责：** 小组名、成员名与 worktree 名映射。

**对外接口：**

```python
MEMBER_NAME_RESERVED = "lead"
NAME_MAX = 24
WORKTREE_PREFIX = "team-"

def validate_team_name(name: str) -> str
def validate_member_name(name: str) -> str
def member_worktree_name(team: str, member: str) -> str
```

规则：1–24 字符、单段、小写 `[a-z0-9][a-z0-9._-]*`、不以点/空格结尾、
拒绝 `.`/`..`、Windows 保留名；成员名不得为 `lead`。
`member_worktree_name` 产出 `team/<team>/<member>`（最长 54），并再走
state18 的 `validate_name` 校验，确保与 worktree 命名规则一致。

**依赖：** `codepilot.worktree.naming.validate_name`（仅校验，不产生反向依赖）。

### `codepilot/teams/settings.py`

**职责：** 读取 `config.yaml` 的 `team:` 段并校验。

**对外接口：**

```python
def load_team_settings(config_path: str | Path) -> TeamSettings
def select_backend(requested: str, settings: TeamSettings) -> TeamMemberBackend
```

- 缺文件/缺段 → 全默认。
- `root` 支持 `~` 并 `expanduser/resolve`；数值字段必须 > 0；非法 → `TeamSettingsError`。
- `select_backend`：`auto` → `inprocess`；`inprocess` → 通过；`terminal`/未知 →
  `TeamBackendError`（state19 明确不实现终端后端）。

**依赖：** `yaml`、`model`。

### `codepilot/teams/locks.py`

**职责：** 跨平台文件锁。

实现：
- 以 `O_CREAT | O_EXCL | O_WRONLY` 创建锁文件，内容为 `pid + token + 时间`。
- 拿不到锁 → 重试；mtime 超过 `stale_after` → 视为过期，删除后重试；
  总等待超过 `timeout` → `TeamLockError`。
- 释放时读取 token，只有匹配才删除，避免误删被接管的锁。
- `clock/sleep` 可注入；Windows 不依赖 `fcntl`。

**依赖：** 标准库、`model.TeamLockError`。

### `codepilot/teams/storage.py`

**职责：** 持久化布局与原子 IO。

**持久化布局：**

```text
<team_root>/                       # 默认 ~/.config/codepilot/teams
├── current.json                   # {"name": "<当前组>"}
├── locks/current.lock
└── <team>/
    ├── team.json
    ├── registry.json
    ├── tasks.json
    ├── locks/team.lock
    ├── members/<name>.json
    └── mailboxes/
        ├── lead.jsonl
        ├── lead.lock
        ├── <member>.jsonl
        └── <member>.lock
```

- `atomic_write_json`：同目录临时文件 + `os.replace`；`ensure_ascii=False`。
- `read_json`：缺失返回默认；非法 JSON 抛 `TeamError` 或按调用方要求隔离。
- `read_jsonl`：逐行解析，坏行跳过并返回告警计数。
- `rewrite_jsonl`：锁外调用方负责加锁；内部仍走原子替换。
- 损坏的 `tasks.json`：改名 `tasks.json.corrupt-<ts>`，以空任务板继续并警告。
- 损坏的成员元数据：跳过、记日志；注册表重建时排除。

**依赖：** 标准库、`model`、`settings`。

### `codepilot/teams/tasks.py`

**职责：** 任务板 CRUD、依赖门控、领取竞争、状态机。

- 所有写操作在 `team.lock` 内：读 `tasks.json` → 校验/修改 → 原子写回。
- 创建：标题非空；依赖去重、必须存在、拒绝自依赖；assignee 必须是可指派成员。
- `claim`：存在、pending、未指派、全部依赖 done → 写 assignee；**不改 in_progress**。
  返回具体原因：不存在/已被指派/依赖未完成/状态不允许。
- 状态机：`pending → in_progress | cancelled`；`in_progress → done | cancelled`；
  `done/cancelled → pending` 只允许 Lead 重开。
- 删除：被其他任务依赖 → 拒绝；`in_progress` → 拒绝。
- `done` 时写 `result`。
- 依赖判定纯函数：`deps_satisfied(task, all_tasks) -> (bool, list[int])`。

**依赖：** `storage`、`locks`、`model`。

### `codepilot/teams/registry.py`

**职责：** 名称注册表（两段式投递第一段）与成员元数据更新。

- `team.json` 花名册 + `members/*.json` 为真源；`registry.json` 为派生索引。
- 启动/加载时：缺失、损坏或不一致 → 从成员文件重建并记日志。
- `resolve(name)`：`lead` 返回合成引用；成员返回 `MemberRef`。
- `resolve_active(name)`：额外拒绝不存在/terminated。
- `update(name, **fields)`：在 `team.lock` 内同时写成员元数据与注册表。
- 小组加载时把所有非 terminated 成员状态改为 offline（Lead 除外），并持久化。

**依赖：** `storage`、`locks`、`model`、`settings`。

### `codepilot/teams/mailbox.py`

**职责：** 消息校验、摘要、追加、消费。

- 构造：自动 `id`（uuid4 hex）、`timestamp`、`read=False`、`summary`
  （正文首行压缩空白、截断到 `message_summary_chars`）。
- 类型校验见 F7：`text`/`broadcast` 需要正文；`plan_request` 需要 task_id+正文；
  `plan_decision` 需要 task_id+approved(bool)+reason；`status` 需要合法状态值。
- `append`：邮箱锁内一次写入一行 + `flush/fsync`；锁失败抛 `TeamLockError`，
  不产生半行。
- `consume_unread(limit)`：锁内读取、标记已读、原子重写、返回消息。
- `consume_matching(type, task_id, limit)`：只消费匹配的未读消息，其余保持未读
  （审批专用）。
- `peek`：不改变已读状态。
- 坏行跳过并记录；不因单条坏消息导致邮箱不可用。

**依赖：** `storage`、`locks`、`model`、`settings`。

### `codepilot/teams/prompts.py`

**职责：** 团队相关提示文本（纯函数）。

```python
def lead_context_block(team: TeamMeta | None, members: list[MemberRef],
                       task_counts: dict[str, int]) -> str
def member_context_block(team: TeamMeta, member: TeamMember,
                         roster: list[MemberRef]) -> str
def task_execution_prompt(task: Task, *, unread: list[TeamMessage],
                          approved_plan: str = "") -> str
def task_planning_prompt(task: Task) -> str
```

- Lead 块：当前组、成员状态、任务计数、工具使用提示；无组时提示 `team_create`。
- 成员块：组、成员名、角色、worktree 路径、分支、审批要求、花名册、
  「消息在任务边界消费」「无 Lead 编排工具」。
- 任务提示：任务 ID/标题/描述/依赖/结果要求；执行时附已批准计划与未读消息。
- 计划提示：只制定计划，不修改文件、不执行命令。

**依赖：** `model`，仅标准库。

### `codepilot/teams/runtime.py`

**职责：** 长期成员运行时。

**构造依赖：** 已构建好的 `Agent`、`Mailbox`、`TaskBoard`、`TeamManager`、
`TeamMember`、`TeamMeta`、`TeamSettings`；`clock/sleep/agent_factory` 可注入。

**核心行为：**

- `start()`：创建 asyncio task，进入 idle，等待 wake。
- `wake()`：设置事件；任务指派、消息到达、审批决定、显式唤醒都调用。
- 主循环：
  1. 等待 wake；
  2. `consume_unread(limit)` 消费任务边界消息；
  3. 读取 `assignee=自己` 且 `pending` 的任务（按 ID 升序）；
  4. 无任务无消息 → idle；
  5. 对每个任务执行「审批（可选）→ 执行」；
  6. 全部完成 → idle + status 通知。
- 计划回合：`phase="plan"`；tool_filter 只保留无副作用工具与只读任务工具；
  输出文本作为计划。
- 审批：发 `plan_request` → blocked → 等待 `plan_decision`（`consume_matching`），
  等待间隔 `approval_poll_seconds`；总超时 `approval_timeout_seconds` 后
  发 status(blocked) 并继续等待后续 decision。
- 执行回合：`phase="execute"`；把任务提示追加到长期 `self._msgs` 后调用
  `agent.run(self._msgs)`，复用 `_collect` 风格的消费逻辑。
- 成功：任务 done + result；失败/轮次上限：任务保持 in_progress，成员
  failed/blocked 并通知 Lead，不伪造 done。
- `stop()`：设置终止标志、唤醒、`task.cancel()`；收尾时释放 worktree 占用锁、
  状态 terminated；尽力发送 status 通知。

**依赖：** `Agent`、`model`、`prompts`、`mailbox`、`tasks`、`manager`（只调用门面方法）。

### `codepilot/teams/tools_lead.py`

**职责：** Lead 编排工具，只注册到 Lead registry。

工具与参数：

```text
team_create(name)
team_list()
team_use(name)
team_show(name=None)
team_spawn(name, role, backend="auto", requires_approval=False)
team_terminate(name)
team_send(to, body)
team_broadcast(body)
team_decide(task_id, approved, reason="")
team_inbox(limit=20)
team_task_create(title, description="", depends_on=[], assignee=None)
team_task_update(task_id, title=None, description=None, depends_on=None,
                 assignee=None, status=None)
team_task_list(status=None, assignee=None)
```

- 全部 `system_exempt=True`；写操作 `side_effect=True`。
- `team_decide` 通过 `pending_approvals[task_id]` 定位成员；找不到 → 明确错误。
- `team_inbox` 消费 Lead 未读消息，返回结构化文本。
- 异常统一转 `ToolResult(error=...)`，不抛到 Agent 循环。

**依赖：** `manager`、`model`、`tools.base`。

### `codepilot/teams/tools_member.py`

**职责：** 成员协作工具，只注册到成员 registry。

工具与参数：

```text
task_list(status=None, assignee=None)
task_get(task_id)
task_create(title, description="", depends_on=[])
task_update(task_id, title=None, description=None, depends_on=None, status=None)
task_delete(task_id)
task_claim(task_id)
send_message(to, body)
broadcast(body)
```

- 成员 `task_update` 不允许修改 assignee。
- `send_message` 使用 `text` 类型；`broadcast` 使用 `broadcast` 类型。
- 审批计划由运行时内部发送，不暴露为工具。
- 读类工具 `side_effect=False`；写类为 True。

**依赖：** `manager`、`model`、`tools.base`。

### `codepilot/teams/manager.py`

**职责：** 小组生命周期、成员生命周期、消息/任务门面、事件分发、关闭。

- 小组：`create_team/list_teams/use_team/current_team`，操作 `current.json` 与
  `team.json`；创建时初始化 lead 注册项、空任务板、lead 邮箱。
- 加载：读取当前组 → 重建注册表（如需）→ 非 terminated 成员置 offline。
- 派生：校验 → spawning → worktree enter（to_thread）→ 构造 Agent/运行时 →
  start → idle；失败回滚。
- 终止：runtime.stop → worktree.release → terminated；保留目录/分支。
- 任务门面：委托 TaskBoard；`assign` 时唤醒成员。
- 消息门面：解析注册表 → 追加邮箱 → 唤醒/事件；Lead 消息发 `TeamEvent`。
- `pending_approvals: dict[int, str]`：记录等待审批的任务与成员。
- `set_on_event`：App 注入回调；事件只做提示，内容真源在邮箱。
- `shutdown`：取消全部 runtime，释放 worktree 占用锁（保留文件）。

**依赖：** 领域层、`runtime`、`prompts`，不 import UI。

## 模块交互

### 建组 / 切组

```text
用户 → /team create | Lead team_create
      → TeamManager.create_team
      → TeamStorage 建目录 + team.json + registry.json + tasks.json + lead 邮箱
      → current.json 原子写
```

### 派生成员

```text
Lead team_spawn(name, role, backend, requires_approval)
  → TeamManager 校验组/名/角色/后端
  → 注册表 + 成员元数据 status=spawning
  → asyncio.to_thread(WorktreeManager.enter, "team/<team>/<member>")
  → 构造 Agent（隔离 registry + 成员工具 + 团队提示）与 MemberRuntime
  → runtime.start()（asyncio task）
  → status=idle；返回成员快照
失败 → 回滚本次新建的 worktree/元数据/注册表，release 占用
```

### 任务派发 / 领取

```text
Lead team_task_create / team_task_update(assignee) → TaskBoard → 唤醒成员 runtime
成员 task_claim → TaskBoard.claim（依赖门控 + 原子领取）
成员 runtime 醒来 → 取 assignee=自己的 pending 任务 → 开始
```

### 成员消息

```text
成员 send_message / broadcast
  → TeamManager.send / broadcast
  → MemberRegistry.resolve_active（第一段）
  → Mailbox.append（第二段，邮箱锁）
  → 目标为 Lead：TeamEvent → App 通知队列 → Lead 调 team_inbox 读全文
  → 目标为成员：runtime.wake()（消息在任务边界消费）
```

### 审批

```text
runtime 领取需审批任务
  → 只读计划回合（无写工具）
  → Mailbox.append(plan_request) 给 Lead
  → status=blocked，等待
Lead 收到事件 → team_inbox → team_decide(task_id, approved, reason)
  → TeamManager 按 pending_approvals[task_id] 找成员
  → Mailbox.append(plan_decision) → runtime.wake()
runtime 在审批等待中 consume_matching(plan_decision, task_id)
  → 批准：status=running → 执行
  → 驳回：任务回 pending、清 assignee、status=idle
  → 超时：status=blocked + status 通知，继续等待后续 decision
```

### 完成 / 空闲 / 终止

```text
runtime 执行回合结束
  → 成功：TaskBoard.update(done, result) → status=idle + status 消息给 Lead
  → 失败/轮次上限：任务保持 in_progress → status=failed|blocked + 通知
Lead team_terminate
  → runtime.stop()（task.cancel + 等待收尾）
  → WorktreeManager.release(handle)（保留目录/分支）
  → status=terminated + 事件
```

### 进程重启

```text
main → TeamSettings → TeamManager.load_current
  → 加载 team/tasks/mailboxes/registry（缺失则重建）
  → 所有非 terminated 成员状态 → offline 并持久化
  → 不自动派生、不自动恢复上下文
```

### 退出

```text
App 退出 → await TeamManager.shutdown()
  → 取消全部成员 runtime
  → WorktreeManager.release 所有句柄（不删目录）
```

## 文件组织

```text
codepilot/teams/                      （新建）
├── __init__.py                       — 包导出（TeamManager/TeamSettings/错误）
├── model.py                          — 枚举 + TeamMeta/TeamMember/Task/TeamMessage/TeamEvent
├── naming.py                         — 小组/成员名校验、worktree 名映射
├── settings.py                       — TeamSettings + load_team_settings（读取 config.yaml team:）
├── locks.py                          — FileLock（重试/过期/令牌校验）
├── storage.py                        — 路径布局、原子 JSON、JSONL 读写、损坏隔离
├── tasks.py                          — TaskBoard + 依赖纯函数 + 状态机
├── registry.py                       — MemberRegistry（解析/更新/重建/离线标记）
├── mailbox.py                        — 消息校验、摘要、追加、消费、匹配消费
├── manager.py                        — TeamManager（小组/成员/消息/任务门面/事件/关闭）
├── runtime.py                        — MemberRuntime（长期 Agent、任务循环、审批、idle/wake）
├── prompts.py                        — 成员团队块、Lead 团队块、任务/计划提示
├── tools_lead.py                     — Lead 编排工具（13 个）
└── tools_member.py                   — 成员协作工具（8 个）

修改：
├── codepilot/tools/registry.py       — 增加 clone()
├── codepilot/worktree/manager.py     — 增加 release(handle)
├── codepilot/main.py                 — base/lead registry、TeamManager、工具/App/命令装配
├── codepilot/ui/app.py               — team_manager 注入、TeamEvent 队列与回灌
├── codepilot/commands/builtins.py    — /team 命令
├── config.example.yaml               — team: 段示例
└── docs/decisions.md                 — 视实现结果补 ADR（若产生新的关键裁决）

tests/teams/                          （新建）
├── conftest.py                       — 临时 team root、假时钟、假 provider/agent 工厂
├── test_naming.py
├── test_settings.py
├── test_locks.py
├── test_storage.py
├── test_tasks.py
├── test_registry.py
├── test_mailbox.py
├── test_runtime.py
├── test_approval.py
├── test_manager.py
├── test_tools_lead.py
├── test_tools_member.py
├── test_visibility.py
├── test_commands.py
└── test_end_to_end.py

修改测试：
├── tests/test_commands_builtins.py   — /team 命令与工具集
├── tests/test_subagent_app.py 等     — 如有工具集断言，补 Lead 工具排除
└── tests/worktree/test_manager.py    — release()
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 工具面隔离 | 两套 registry（base / lead）+ 成员独立 registry | 复用现有 `ToolRegistry` 与 `tool_filter`，无需给 Agent 增加权限系统；普通子 Agent 从结构上不可能看到 Lead/成员工具 |
| 成员 Agent | 长期存活、复用同一 `msgs` 列表 | `Agent.run(messages)` 会原地追加消息，天然支持多任务上下文连续；不必重写 runner |
| 成员任务驱动 | 事件唤醒 + 任务板轮询 | 同进程用 `asyncio.Event` 即时唤醒；任务板是唯一真源，state20 可换成轮询/独立进程 |
| 锁 | O_EXCL 锁文件 + mtime 过期 + token 释放 | Windows 无 POSIX flock；锁文件语义跨平台，且满足“重试/过期”要求 |
| 原子写 | 临时文件 + `os.replace` | 避免半写 JSON；Windows 上同样是原子替换 |
| 邮箱读状态 | JSONL + 锁内重写 | 消息格式直观、可审计；规模按数百条设计，重写成本可接受 |
| 注册表 | 派生索引，成员元数据为真源 | 避免两份数据漂移；损坏时可从 `members/*.json` 重建 |
| worktree 命名 | `team/<team>/<member>` | 与 state17 命名空间隔离；长度受限可校验；一个成员一个固定目录 |
| 成员 worktree 生命周期 | idle/blocked/running 期间保持占用，终止/退出时 release 保留 | 防止同名成员被重复派生；state20 合并需要保留改动 |
| 后端选择 | auto → inprocess；显式 terminal 报错 | 不静默降级；state20 再扩展真实终端后端 |
| 审批计划 | 独立只读计划回合 + 内部 plan_request | 计划确实由成员生成，且不可能在批准前写文件；无需额外成员工具 |
| 审批超时 | blocked + 继续等待后续 decision | 不丢任务、不自动开工；Lead 仍可补救 |
| 消息消费 | 任务边界；审批 decision 例外 | 符合“不做实时通信”；审批必须能在阻塞中推进 |
| Lead 通知 | 邮箱真源 + App 通知队列 + `team_inbox` 读全文 | 避免长计划塞爆主上下文；复用 state17 的流式安全回灌模式 |
| 成员系统工具 | 排除 `agent` 与 `load_skill`，保留 MCP | 防止嵌套委派与共享 SkillManager 状态污染；MCP 工具是外部能力，不涉及团队状态 |
| 权限询问 | TeamManager 包一层 asyncio.Lock 串行化 human_callback | 多成员同时请求权限时避免 TUI 模态屏竞争 |
| 名称长度 | 小组/成员各 ≤24 字符 | 与 `team/<team>/<member>` 的 64 字符 worktree 限制相容，避免哈希映射 |
| 计划文档 | 不做全局 TUI 面板，`/team show` + 日志 + 工具结果 | 控制本阶段范围；观察视图可在后续阶段补 |

## 风险与已知边界

1. **长期 Agent 占用内存与上下文**：每个成员一个 Agent + 消息历史；本阶段按数十成员设计，
   超出需后续做空闲卸载。成员上下文压缩沿用 `ContextManager`。
2. **进程内并发 API 调用**：多个成员同时流式请求同一 provider；按 provider 现有能力处理，
   不引入限流。
3. **权限模态竞争**：用共享锁串行化；若真实 TUI 仍冲突，验收时记录并降级为“成员默认
   auto 权限”提示。
4. **Hook 共享**：Hook command 仍在进程 cwd 执行（state18 已知边界），团队成员共享 Hook
   引擎；本阶段不隔离。
5. **锁过期接管**：操作耗时必须远小于 `lock_stale_seconds`；worktree Git 操作在锁外执行。
6. **消息/邮箱增长**：消费会重写文件，状态通知可能累积；按规模边界接受，不做归档。
7. **审批状态内存化**：`pending_approvals` 只在 TeamManager 内存中；进程重启后成员 offline，
   审批自然失效，需 Lead 重新指派。
8. **成员失败后不自动重启**：任务保持 in_progress，由 Lead 重新指派或终止。
9. **不做真实终端隔离**：同进程成员仍共享 Python 进程与全局状态（MCP、Hook、provider）；
   强隔离在 state20。
10. **state19 不合并**：成员改动留在各自 worktree，Lead 报告路径/分支；分支/目录清理依赖
    state18 的 TTL 与人工操作。

## Spec 覆盖对照

| Spec | Plan 落点 |
|------|-----------|
| F1 小组生命周期 | `manager.py` + `storage.py` + `registry.py` |
| F2 花名册与派生 | `manager.py` + `model.py` + `worktree/manager.release` |
| F3 运行后端 | `settings.py` + `manager.spawn_member` |
| F4 工具面隔离 | base/lead registry + `tools_lead.py` / `tools_member.py` |
| F5 共享任务板 | `tasks.py` |
| F6 邮箱与注册表 | `registry.py` + `mailbox.py` + `locks.py` |
| F7 结构化消息 | `model.py` + `mailbox.py` |
| F8 审批闭环 | `runtime.py` + `tools_lead.team_decide` |
| F9 空闲/唤醒/终止 | `runtime.py` + `manager.py` |
| F10 Lead 编排 | `tools_lead.py` + `manager.py` |
| N1/N2 | `locks.py` + `storage.py` + `naming.py` |
| N3/N4 | 全模块日志与错误类型 + runtime 故障隔离 |
| N5/N6/N7 | registry 分层、可注入设置、临时目录测试 |
