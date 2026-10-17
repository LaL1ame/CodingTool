# Worktree 工作目录隔离 Plan

## 架构概览

本阶段分为五个边界清晰的层次：

1. **Worktree 领域层**（新包 `codepilot/worktree/`）
   只关心 Git worktree 与文件系统：命名校验、配置读取、Git 命令封装、元数据、环境初始化、生命周期编排与过期清理。不 import Agent / UI / Textual。

2. **子 Agent 隔离桥接层**（新模块 `codepilot/subagents/isolation.py`）
   把 Worktree 句柄翻译成子 Agent 可用的运行环境：独立 ToolRegistry、EnvContext、PermissionManager、ContextManager、MemoryInjector 与路径说明块。依赖 worktree 领域层，但不反向依赖。

3. **委派编排层**（修改 `codepilot/subagents/manager.py` / `runner.py` / `tool.py`）
   在委派入口解析 `worktree` 参数，调用 `enter` 获取句柄，传给 runner；任务结束统一 `leave` 做保留/清理决策，并把结果写进任务摘要。

4. **装配与入口层**（修改 `main.py`、`ui/app.py`、`commands/builtins.py`）
   启动时加载 `.worktree.yaml`、构造 WorktreeManager、注入 SubAgentManager 与 App；启动后台清理协程；注册 `/worktree` 命令。

5. **扫描隔离层**（修改 `memory/resolver.py`、`skills/resolver.py`、`subagents/loader.py`、`tools/glob_tool.py`、`tools/grep_tool.py`）
   防止主 Agent 的递归发现与搜索进入 `.codepilot/worktrees`。

## 核心数据结构

### WorktreeSettings

```python
@dataclass(frozen=True)
class WorktreeSettings:
    ttl_hours: float = 24.0
    cleanup_interval_minutes: float = 60.0
    copy: tuple[str, ...] = (
        "config.yaml",
        ".hooks.yaml",
        "codepilot-permissions.local.yaml",
    )
    symlink: tuple[str, ...] = (".venv", "venv", "node_modules")
    include: tuple[str, ...] = ()
```

`load_settings(workspace: Path) -> WorktreeSettings` 读取项目根 `.worktree.yaml` 的 `worktree:` 段。
文件缺失 → 全默认；字段类型/范围非法 → 启动期抛 `WorktreeSettingsError`（fail-fast）。
`copy`/`symlink`/`include` 采用**覆盖**语义：项目写了该字段就整体替换默认列表，没写才用默认。

### WorktreeMeta

```python
@dataclass(frozen=True)
class WorktreeMeta:
    name: str
    branch: str
    base: str          # 创建/挂接时记录的基线 commit
    created_at: float
    role: str | None
    task_id: int
```

存放于 `<worktree_root>/_meta/<sha1(name)>.json`，与工作目录分离，避免污染工作树状态。
`_meta` 以下划线开头，无法通过名称校验，不会与合法 worktree 名冲突。

### WorktreeHandle

```python
@dataclass(frozen=True)
class WorktreeHandle:
    name: str
    path: Path
    branch: str
    base: str
    created_at: float
    reused: bool       # 目录已存在、快速恢复
    attached: bool     # 挂到已有分支
```

### WorktreeEntry / ExitResult / CleanupReport

```python
@dataclass(frozen=True)
class WorktreeEntry:
    name: str
    path: Path
    branch: str
    base: str
    created_at: float
    dirty: bool
    ahead: int
    locked_by: int | None

@dataclass(frozen=True)
class ExitResult:
    kept: bool
    reason: str          # kept/dirty、kept/ahead、removed/clean ...
    path: Path
    branch: str

@dataclass(frozen=True)
class CleanupReport:
    removed: tuple[str, ...]
    skipped: tuple[tuple[str, str], ...]   # (name, reason)
```

### RetentionDecision

```python
@dataclass(frozen=True)
class RetentionDecision:
    keep: bool
    reason: str

def decide_retention(*, dirty: bool, ahead: int) -> RetentionDecision
```

纯函数：`dirty or ahead > 0` → 保留；否则清理。供 leave、rm、cleanup 三处复用。

## 模块设计

### `codepilot/worktree/naming.py`

**职责：** 名称安全校验与自动命名。

**对外接口：**

```python
class WorktreeNameError(ValueError): ...

def validate_name(name: str) -> str
def auto_name(role: str, task_id: int) -> str
```

规则：

- 总长 ≤64；最多 3 段；每段 1–32。
- 段字符集 `[a-z0-9._-]`，且段首必须是字母或数字。
- 拒绝空段、`.`、`..`、反斜杠、绝对路径、前后空白。
- 拒绝 Windows 保留名（CON/PRN/AUX/NUL/COM1-9/LPT1-9，含扩展名形式）。
- 拒绝段尾点与段尾空格。
- `auto_name`：角色名 slug 化为小写 `[a-z0-9-]`，追加 `-<task_id>`，再走 `validate_name`。
- 最终路径包含校验在 manager 中做：`(root / name).resolve().is_relative_to(root.resolve())`。

**依赖：** 仅标准库。

### `codepilot/worktree/settings.py`

**职责：** 读取并校验 `.worktree.yaml`。

**对外接口：**

```python
class WorktreeSettingsError(Exception): ...

def load_settings(workspace: Path) -> WorktreeSettings
```

校验规则：`ttl_hours > 0`、`cleanup_interval_minutes > 0`；规则列表必须是字符串列表；
拒绝绝对路径与含 `..` 段的规则；`copy/symlink/include` 各自覆盖默认值。

**依赖：** `yaml`。

### `codepilot/worktree/git.py`

**职责：** Git 子进程封装的唯一出口。

**对外接口：**

```python
class GitError(RuntimeError): ...

class GitRunner:
    def __init__(self, main_root: Path) -> None
    def run(self, args: Sequence[str], *, cwd: Path | None = None,
            check: bool = True) -> str
    def toplevel(self) -> Path | None
    def head(self) -> str
    def branch_exists(self, branch: str) -> bool
    def add_worktree(self, path: Path, branch: str, base: str) -> None
    def attach_worktree(self, path: Path, branch: str) -> None
    def remove_worktree(self, path: Path, force: bool = False) -> None
    def delete_branch(self, branch: str) -> None
    def status_porcelain(self, path: Path) -> str
    def commits_ahead(self, base: str, rev: str = "HEAD") -> int
    def merge_base(self, a: str, b: str) -> str | None
    def is_ignored(self, path: Path) -> bool
    def hooks_path(self) -> str | None
    def set_worktree_config(self, path: Path, key: str, value: str) -> None
```

实现约束：`subprocess.run` 参数列表、`shell=False`、`capture_output=True`、显式 `cwd`；
非零退出统一抛 `GitError`（含命令与 stderr）。所有方法同步，便于命令层直接调用；
异步调用方用 `asyncio.to_thread` 卸载。

**依赖：** 标准库。

### `codepilot/worktree/meta.py`

**职责：** 元数据 JSON 的读写与路径计算。

**对外接口：**

```python
def meta_path(root: Path, name: str) -> Path
def read_meta(root: Path, name: str) -> WorktreeMeta | None
def write_meta(root: Path, meta: WorktreeMeta) -> None   # 原子写
def delete_meta(root: Path, name: str) -> None
def iter_metas(root: Path) -> list[WorktreeMeta]
```

**依赖：** `WorktreeMeta`、标准库。

### `codepilot/worktree/initializer.py`

**职责：** 创建后的环境初始化与 Git hooks 修正。

**对外接口：**

```python
class WorktreeInitError(Exception): ...

class Initializer:
    def __init__(self, main_root: Path, settings: WorktreeSettings,
                 git: GitRunner) -> None
    def initialize(self, wt_path: Path) -> None
```

流程：

1. **copy**：按规则 glob 主仓；目标已存在则跳过；文件 `copy2`，目录 `copytree`。
2. **include**：与 copy 相同，但仅复制 `git.is_ignored(src)` 为真的匹配项。
3. **symlink**：目标为目录；目标已存在则跳过；优先 `os.symlink`，
   Windows 权限不足时回退 `cmd /c mklink /J`（junction）。
4. **hooks**：
   - 若主仓存在 `.githooks/`：在 worktree 内建软链并设置 worktree 级
     `core.hooksPath` 指向主仓绝对路径。
   - 否则若主仓 `core.hooksPath` 是相对路径：解析为主仓绝对路径，设置 worktree 级
     `core.hooksPath`；设置前启用 `extensions.worktreeConfig`。
   - 其余情况什么都不做（依赖 Git 共享 hooks）。
5. 每个匹配源路径必须 `resolve()` 后仍在主仓内；规则本身已在 settings 层拒绝 `..` 与绝对路径。

失败分级：源不存在/glob 无匹配 → 跳过并记日志；路径逃逸/规则非法/复制或链接 IO 失败
→ 抛 `WorktreeInitError`，由 manager 清理半成品。

**依赖：** `GitRunner`、`WorktreeSettings`、标准库。

### `codepilot/worktree/manager.py`

**职责：** 生命周期编排、并发锁、保留决策、删除与过期清理。

**对外接口：**

```python
class WorktreeError(RuntimeError): ...

class WorktreeManager:
    def __init__(self, main_root: Path, settings: WorktreeSettings | None = None,
                 git: GitRunner | None = None) -> None

    @property
    def root(self) -> Path
    @property
    def settings(self) -> WorktreeSettings

    def enter(self, name: str | None, *, role: str, task_id: int) -> WorktreeHandle
    def leave(self, handle: WorktreeHandle) -> ExitResult
    def remove(self, name: str, *, force: bool = False) -> ExitResult
    def list_status(self) -> list[WorktreeEntry]
    def cleanup_expired(self, *, now: float | None = None) -> CleanupReport
    async def run_periodic_cleanup(self) -> None
```

内部状态：

- `_lock: threading.RLock` 保护所有公开方法与 `_active`（`to_thread` 会并发进入）。
- `_active: dict[str, int]` 名称 → 占用 task_id。

`enter` 流程：

1. 校验主仓根等于 Git toplevel；否则 fail-closed（本阶段要求从仓库根启动）。
2. `validate_name(name or auto_name(role, task_id))`；路径包含校验。
3. 加锁；`_active` 已占用 → 抛 `WorktreeError`（含占用者 task_id）。
4. 目录存在 → 三检（目录、`.git` 是文件、元数据存在且名字匹配）→ 通过则返回
   `reused=True`；任一失败 → 抛错，不接管。
5. 目录不存在：
   - 分支存在 → `attach_worktree`（`attached=True`），base 取元数据或 `merge-base(HEAD, branch)`，
     失败回退当前 HEAD（保守地把分支提交视为新提交）。
   - 分支不存在 → 记录 `head()` 为 base，`add_worktree(path, branch, base)`。
6. 写元数据 → 初始化环境 → 返回句柄。
7. 任一步失败：释放锁；若本次新建则删除目录、分支（仅本次新建）、元数据；抛出/包装错误。

`leave` 流程：

1. 读取 `status_porcelain` 与 `commits_ahead(base)`。
2. `decide_retention`：保留 → 返回 `ExitResult(kept=True)`；清理 → 删除目录、分支、元数据。
3. 释放 `_active`；清理失败也释放锁并返回带原因的保留/错误信息（记日志）。

`remove` 流程：

1. 解析名称与路径；占用中 → 拒绝。
2. 三检；陌生目录 → 拒绝。
3. 非 force：`dirty or ahead>0` → 拒绝并说明；否则删目录+分支+元数据。
4. force：删目录（`git worktree remove --force`），**保留分支**，删元数据。

`cleanup_expired` 流程：

1. 遍历 `_meta`；按 `created_at` 过滤超过 `ttl_hours` 的项。
2. 三层过滤：路径包含 → `.git` 文件/分支前缀/元数据 → 未占用、clean、ahead==0。
3. 通过者走与 leave 相同的清理；任何单项异常只记录并加入 skipped。
4. 返回 `CleanupReport`。

`run_periodic_cleanup`：立即执行一次 `cleanup_expired`，随后每
`cleanup_interval_minutes` 分钟执行；整体用 `asyncio.to_thread` 调用同步实现。

**依赖：** `naming`、`settings`、`git`、`meta`、`initializer`、`model`。

### `codepilot/subagents/isolation.py`

**职责：** 隔离子 Agent 的运行时装配。

**对外接口：**

```python
@dataclass
class IsolatedRuntime:
    provider: BaseProvider
    registry: ToolRegistry
    workspace: Path
    env: EnvContext
    permission: PermissionManager
    context_manager: ContextManager
    skill_injector: Callable[[], list[str]]

def build_isolated_runtime(shared: SharedContext, role: AgentRole,
                           handle: WorktreeHandle) -> IsolatedRuntime

def build_isolated_registry(template: ToolRegistry, workspace: Path) -> ToolRegistry

def isolation_prompt_block(handle: WorktreeHandle) -> str
```

- `build_isolated_registry`：新建 Read/Write/Edit/Glob/Grep/Run 六个工具实例绑定 worktree；
  从模板 registry 复制其余工具（agent、load_skill、MCP 等）的同一实例。
- `permission`：独立 `PermissionManager(workspace=handle.path, mode=...)`，
  `human_callback` 从父权限继承（复用 state17 语义）。
- `context_manager`：`offload_dir=handle.path / ".codepilot" / "context"`。
- `skill_injector`：`[isolation_prompt_block(handle), role.body, *MemoryInjector(handle.path).build()]`。
  项目指令与记忆均从 worktree 读取；用户级记忆仍按 `NoteStore` 默认用户目录。
- `provider`：沿用 `_role_provider` 的角色 model 覆盖逻辑。

路径说明块内容：

```text
【隔离工作目录】
你的工作目录是：<绝对路径>
分支：codepilot/<name>
所有文件读写与命令都限定在此目录；不要引用或修改主工作目录。
需要交接的改动请留在本目录，并在结论中说明。
```

**依赖：** worktree 模型、tools、permissions、context、memory、prompt。

### 修改 `codepilot/subagents/model.py`

- `AgentRole` 增加 `isolation: str | None = None`（当前仅 `"worktree"`）。
- `SubAgentTask` 增加 `worktree: str = ""`（名称，供任务摘要/面板展示）。

### 修改 `codepilot/subagents/loader.py`

- 解析 `isolation`：缺省 `None`；字符串 `worktree` 合法；其他值抛 `RoleError`。
- `_SKIP` 加入 `.codepilot`，避免扫描到 worktree 内的角色文件。

### 修改 `codepilot/subagents/tool.py`

- `parameters` 增加可选 `worktree`（string）。
- `execute` 透传给 `manager.delegate`。
- 描述中说明：仅 `isolation: worktree` 的角色可传；命名规则与自动命名规则。

### 修改 `codepilot/subagents/runner.py`

- `run_role_agent(..., worktree: WorktreeHandle | None = None)`。
- 传入句柄时调用 `build_isolated_runtime`，用其 provider/registry/workspace/env/permission/
  context_manager/skill_injector 构造 Agent；未传时保持现有路径完全不变。
- `run_fork_agent` 不接收 worktree（本阶段不支持 Fork 隔离）。

### 修改 `codepilot/subagents/manager.py`

- 构造函数注入 `worktree: WorktreeManager | None`，公开只读属性 `worktree`。
- `delegate(..., worktree: str | None = None)`：
  - 非定义式、非隔离角色、或未启用 WorktreeManager 却传参 → 返回明确错误；
  - `task.worktree = worktree or auto_name(role, task_id)`（隔离角色）。
- `_run_one`：
  - 隔离任务先 `await asyncio.to_thread(worktree.enter, ...)`，失败 → `SubAgentError`；
  - 把句柄传给 `run_role_agent`；
  - `finally` 中 `await asyncio.shield(asyncio.to_thread(worktree.leave, handle))`，
    把 `ExitResult` 追加到 `task.summary` 或 `task.error`，并确保锁释放；
  - 取消路径同样尝试 leave；leave 自身异常只记日志，不掩盖原错误。
- `role_list_block`：隔离角色行追加「（隔离，可用 worktree=名字 指定目录）」。

### 修改 `codepilot/commands/builtins.py` + `codepilot/ui/app.py`

- `build_registry()` 不新增参数；沿用 state17 的 `getattr(ctx, ...)` 降级模式。
- 新增 LOCAL 命令：

```text
/worktree list
/worktree rm <name> [--force]
```

- 输出：名称、分支、路径、占用、dirty、ahead。
- `_CommandContextImpl` 增加 `worktree_manager` 属性，返回 App 持有的 manager；
  App 构造函数增加 `worktree_manager=None` 参数。

### 修改 `codepilot/main.py`

- `load_settings(workspace)`（启动期 fail-fast，错误打印后退出）。
- 构造 `WorktreeManager(workspace, settings)`，注入 `SubAgentManager` 与 `CodePilotApp`。
- `_run_ui` 中启动 `asyncio.create_task(worktree_manager.run_periodic_cleanup())`，
  退出时取消并回收。
- 角色白名单校验不变；隔离角色不要求额外工具。

### 扫描隔离修改

- `codepilot/memory/resolver.py`：`os.walk` 剪枝加入 `.codepilot`。
- `codepilot/skills/resolver.py`：`_SKIP` 加入 `.codepilot`。
- `codepilot/subagents/loader.py`：`_SKIP` 加入 `.codepilot`。
- `codepilot/tools/glob_tool.py` / `grep_tool.py`：结果过滤 `workspace/.codepilot/worktrees`
  子树；相对路径为 `.codepilot/worktrees/...` 的一律跳过。此过滤放在结果收集处，
  不改变 `rglob` 本身的匹配语义。

## 模块交互

### 创建与执行

```text
主 Agent
  └─ agent 工具 (mode=defined, role=architect, worktree=auth-fix)
       └─ SubAgentManager.delegate
            ├─ 校验角色 isolation / 解析名称
            ├─ to_thread(WorktreeManager.enter)
            │    ├─ validate_name + 路径包含
            │    ├─ 占用锁
            │    ├─ 快速恢复 / 新建 / 挂接分支
            │    ├─ Initializer.initialize
            │    └─ write_meta → WorktreeHandle
            └─ run_role_agent(..., worktree=handle)
                 └─ build_isolated_runtime
                      ├─ 独立 registry / env / permission
                      ├─ 独立 ContextManager (offload 在 worktree)
                      └─ role body + 路径说明 + worktree 项目指令/记忆
```

### 结束与保留

```text
子 Agent 结束（成功/失败/取消）
  └─ to_thread(WorktreeManager.leave)
       ├─ git status --porcelain
       ├─ git rev-list --count <base>..HEAD
       ├─ decide_retention
       ├─ keep  → 返回路径/分支/原因
       └─ clean → 删除目录、分支、元数据
  └─ 结果追加到 task.summary / task.error
```

### 后台清理

```text
main._run_ui
  └─ create_task(WorktreeManager.run_periodic_cleanup)
       ├─ 立即 cleanup_expired
       └─ 每 60 分钟 cleanup_expired
            ├─ 遍历 _meta，按 TTL 过滤
            ├─ 三层安全过滤
            └─ 只删除 clean 且无新提交的目录
```

### 管理命令

```text
/worktree list → _CommandContextImpl.worktree_manager → WorktreeManager.list_status
/worktree rm   → WorktreeManager.remove
```

## 文件组织

```text
codepilot/
├── worktree/                    （新建）
│   ├── __init__.py              — 导出 manager / settings / 错误
│   ├── model.py                 — WorktreeMeta/Handle/Entry/ExitResult/CleanupReport
│   ├── naming.py                — validate_name / auto_name
│   ├── settings.py              — WorktreeSettings + load_settings
│   ├── git.py                   — GitRunner + GitError
│   ├── meta.py                  — 元数据读写
│   ├── initializer.py           — copy/symlink/include/hooks
│   └── manager.py               — 生命周期、锁、保留决策、清理
├── subagents/
│   ├── isolation.py             （新建）运行时装配
│   ├── model.py                 （修改）isolation + task.worktree
│   ├── loader.py                （修改）isolation 解析 + 扫描剪枝
│   ├── tool.py                  （修改）worktree 参数
│   ├── runner.py                （修改）隔离运行时接入
│   └── manager.py               （修改）enter/leave/摘要
├── commands/builtins.py         （修改）/worktree 命令
├── ui/app.py                    （修改）worktree_manager 注入
├── main.py                      （修改）装配 + 后台清理
├── memory/resolver.py           （修改）扫描剪枝
├── skills/resolver.py           （修改）扫描剪枝
└── tools/
    ├── glob_tool.py             （修改）结果过滤
    └── grep_tool.py             （修改）结果过滤

tests/
├── worktree/
│   ├── conftest.py              — 临时 Git 仓库 fixture
│   ├── test_naming.py
│   ├── test_settings.py
│   ├── test_git.py
│   ├── test_meta.py
│   ├── test_initializer.py
│   └── test_manager.py
├── subagents/
│   ├── test_isolation.py        （新建）
│   ├── test_loader.py           （修改）isolation 解析
│   ├── test_tool.py             （修改）worktree 参数
│   └── test_manager.py          （修改）enter/leave 集成
├── test_commands_builtins.py    （修改）/worktree
├── test_glob_worktree.py        （新建）扫描隔离
├── test_grep_worktree.py        （新建）扫描隔离
└── test_memory_resolver.py 等   （修改）剪枝断言
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| Worktree 根 | 固定 `.codepilot/worktrees/` | 已被 gitignore；实测 `git worktree add` 可在其内创建；与机器数据同域 |
| Git 调用 | 同步 `subprocess` 封装 + `asyncio.to_thread` | 命令层是同步接口；同步实现易测；异步边界只在一处卸载，避免两套 Git 客户端 |
| 元数据 | 独立 `_meta/<sha1(name)>.json` | 不污染工作树状态；名称哈希避免嵌套/大小写路径问题；`_meta` 不可能是合法名称 |
| 快速恢复 | 目录 + `.git` 文件 + 元数据三检 | 满足“不调 Git”；拒绝接管陌生目录，符合 fail-closed |
| 分支 | `codepilot/<name>`，基于当前 HEAD；存在则挂接 | 与目录名一一对应；保留分支即可续上次工作，也在 `--force` 后避免丢提交 |
| 名称校验 | 小写分段制 | Windows 大小写不敏感，统一小写避免 `Auth`/`auth` 映射同一目录 |
| 工具隔离 | 每 Agent 独立 ToolRegistry | 不改现有工具接口；与“构造时绑定 workspace”的既有架构一致；系统工具共享实例 |
| 路径说明 | 注入 volatile 块 + `EnvContext.cwd` | 模型既看到环境块也看到显式说明，降低引用主仓路径的概率 |
| 项目指令/记忆 | 按 worktree 重建 | 与“绝对路径为 key”一致；worktree 的 `.rules` 与独立笔记目录自然隔离 |
| Skill/Hook | 仍共享 | 本阶段明确不做，避免范围膨胀；在已知边界中记录 |
| 环境规则 | 内置默认 + `.worktree.yaml` 覆盖 | 开箱即用；项目可适配；配置非法启动期报错 |
| include 语义 | 仅复制 `git check-ignore` 命中的匹配项 | 与 copy 区分：copy 是指定文件，include 是“忽略但运行需要”的文件 |
| 并发 | 进程内 RLock + 名称占用表 | 满足单进程内的并行隔离；跨进程锁明确留待后续 |
| 清理 | 24h TTL，启动 + 每小时 | 及时回收崩溃遗留；三层过滤 + TTL 双保险 |
| 删除分支 | 自动清理时删除；`--force` 保留 | 无提交时删分支无损失；有提交时绝不让清理删掉资产 |
| 失败策略 | fail-closed | 隔离失败绝不能悄悄退回主工作区 |
| 仓库根要求 | 主工作区必须等于 Git toplevel | 避免“从子目录启动”导致 worktree 路径与子 Agent 工作根错位；本阶段直接报错，语义明确 |

## 对 Spec 的覆盖

| Spec | 落点 |
|------|------|
| F1 角色声明 | `model.py` / `loader.py` |
| F2 命名校验 | `naming.py` + `manager.enter` 路径包含校验 |
| F3 创建/恢复 | `manager.enter` + `git.py` + `meta.py` |
| F4 进入/上下文 | `subagents/isolation.py` + `runner.py` |
| F5 环境初始化 | `initializer.py` + `.worktree.yaml` |
| F6 退出/保留 | `manager.leave` + `decide_retention` + `manager.py` 摘要 |
| F7 删除保护 | `manager.remove` + `/worktree rm` |
| F8 过期清理 | `manager.cleanup_expired` / `run_periodic_cleanup` + `main.py` |
| F9 管理命令 | `commands/builtins.py` + `ui/app.py` |
| F10 扫描隔离 | `memory/resolver.py`、`skills/resolver.py`、`subagents/loader.py`、`glob_tool.py`、`grep_tool.py` |
| F11 失败边界 | `WorktreeError` / `WorktreeInitError` / manager 错误包装 + fail-closed |

## 已知边界（本阶段不修）

1. **跨进程锁**：进程内占用表不跨 CodePilot 进程；同一仓库多开时依赖 Git 对分支/目录的排他性。
2. **Skill/Hook 共享**：子 Agent 仍可 `load_skill`（state17 已知问题）；Hook command 仍在进程 cwd 执行。
3. **`manager.cancel()`**：仍只改状态不取消 asyncio task（state17 已知问题）；取消任务的 worktree 在协程自然结束后才 leave。
4. **glob/grep 遍历成本**：结果过滤发生在 `rglob` 之后，worktree 很大时遍历仍有成本；本阶段以正确性优先。
5. **子目录启动**：不满足“工作区 == Git toplevel”时隔离直接失败，不做路径映射。
