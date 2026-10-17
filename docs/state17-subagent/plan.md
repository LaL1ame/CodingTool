# state17 SubAgent 系统 Plan

## 架构概览

```
角色文件(.agents/*.md: frontmatter + 正文)
    │
    ▼ loader（四级发现 + 同名覆盖 + 集中校验）
SubAgentManager（known 角色表 + 后台任务注册表 + 委派入口 + 深度/并发上限）
    │ delegate(kind, role?, prompt, background, timeout)
    ▼
runner（构造独立 Agent：共享 provider/registry/hooks/文件系统；独立 permission/context/msgs）
    │
    ├─ 前台：await 子循环跑到底 → 摘要回灌
    └─ 后台：create_task 入后台集合 → 完成经 on_task_done 回调回灌主对话
         ▲
agent 工具（system_exempt 常驻 registry，参数分流 defined/fork）
```

**核心设计取向**（对齐 spec F/N）：
- **子 Agent 复用现有 `Agent` 类**（N6 一致性）——`_run_isolated_skill` 已证明可行。子 Agent = 独立 `Agent` 实例，共享 provider/registry/hooks，注入角色化配置。
- **多层防线落在 `Agent._build_tools` 的新增 `tool_filter` 注入点**——不碰 skill 白名单路径（N1），角色防线叠加在其后。
- **角色正文走 `skill_injector` 注入缝**（env 后最显眼位，每轮重建）——复用 state13/15 机制，不新辟通道（N3）。

## 模块划分（新包 `codepilot/subagents/`）

| 模块 | 职责 | 关键接口 |
|---|---|---|
| `model.py` | 角色数据模型 + 任务模型 | `AgentRole` / `SubAgentTask` / `RoleError` |
| `loader.py` | 四级发现 + frontmatter 解析 + 覆盖 + 集中校验 | `parse_role_file` / `load_all` |
| `manager.py` | 委派入口 + 后台任务注册表 + 深度/并发上限 + 状态追踪 | `SubAgentManager.delegate` / `.tasks` / `.cancel` |
| `runner.py` | 构造独立 Agent 跑到底，收集摘要 | `run_role_agent` / `run_fork_agent` |
| `tool.py` | `agent` 工具（常驻、系统级、参数分流） | `AgentTool` |

**既有改动**：`agent.py`（加 `tool_filter` 参数）、`main.py`（装配）、`ui/app.py`（`/agents` `/bg` 命令 + 回灌接线）、`commands/builtins.py`（注册命令）、`pyproject.toml`（package-data）。

## 核心数据结构

```python
# subagents/model.py
class RoleError(Exception): ...

@dataclass(frozen=True)
class AgentRole:
    name: str                      # 角色名（frontmatter name，唯一）
    description: str               # 用途说明
    body: str                      # 正文 = 子 Agent 系统提示（经 skill_injector 注入）
    tools: frozenset[str]          # 工具白名单（空 = 不约束）
    blocked: frozenset[str]        # 工具黑名单（额外限制，可含 agent）
    model: str | None              # None = 继承当前 provider 模型
    max_rounds: int | None         # None = 继承默认
    permission: str | None         # 权限模式（manual/auto/strict/permissive）；None = 继承主
    can_delegate: bool = False     # 是否放行 agent 工具（F7 显式放行，受深度上限约束）
    path: Path | None = None       # 来源文件
    ordinal: int = 0               # 文件内序号（稳定标识）

@dataclass
class SubAgentTask:
    task_id: int
    kind: str                       # "defined" | "fork"
    role: str | None                # fork 为 None
    status: str                     # running / done / failed / cancelled / backgrounded
    prompt: str
    summary: str = ""               # 完成摘要（回灌用）
    usage: Usage | None = None      # 独立 token 用量
    error: str = ""                 # failed 时原因
    created_at: float = 0.0
```

```python
# agent.py 新增注入点（既有参数全部不变）
tool_filter: Callable[[list[Tool]], list[Tool]] | None = None
# 语义：_build_tools 在 skill_whitelist + plan 过滤之后调用；子 Agent 用它实现
# 多层防线（角色白名单∪系统 − 黑名单 − agent禁 → 后台白名单 → plan 继承）。
```

```python
# subagents/tool.py —— agent 工具参数（JSON Schema 语义）
mode:      "defined" | "fork"     # 分流（必填）
role:      str                    # defined 必需
prompt:    str                    # 任务描述（必填）
background: bool = False          # 显式转后台
timeout:   float | None = None    # 前台超时 → 自动转后台（F8）
```

## 模块设计

### subagents/loader.py
**职责：** 四级发现 + frontmatter 解析 + 同名覆盖 + 集中校验（对齐 F1）。
**对外接口：**
```python
def discover_role_dirs(workspace, *, user_root=None, plugin_root=None, builtin_root=None) -> list[Path]
    # 顺序 = 优先级从低到高：[插件, 内置, 用户, 项目]（后加载覆盖前）
def parse_role_file(path: Path) -> AgentRole          # 单个文件解析，失败抛 RoleError
def load_all(workspace, *, check_tools=None) -> dict[str, AgentRole]
    # 返回 name -> AgentRole（生效版）；坏文件跳过记日志；check_tools 校验白名单存在性
```
**解析：** 复用 Skill 的 `_split_frontmatter` 结构（`---\nYAML\n---` + 正文）。frontmatter 字段：`name/description/tools/blocked/model/max_rounds/permission/can_delegate`；正文 `strip` 后为 `body`。
**校验（启动 fail-fast）：** 缺 `name/description`、`tools`/`blocked` 含未知工具、非法 `permission` 值、非法 `model` 格式 → `RoleError`。
**依赖：** model、yaml。（参考 skills/resolver.py 遍历/剪枝约定，`_SKIP` 目录不深入）

### subagents/manager.py
**职责：** 委派入口 + 后台任务注册表 + 深度/并发上限 + 状态追踪（F8/F10）。
**对外接口：**
```python
class SubAgentManager:
    def __init__(self, known, *, workspace, provider, registry, env, ctx_config,
                 shared_hooks, parent_permission, max_concurrent=4, max_depth=1,
                 notify=None, on_task_done=None): ...
    async def delegate(self, kind, prompt, *, role=None, background=False, timeout=None) -> ToolResult
        # defined：role 必填；fork：role 忽略。返回 agent 工具结果。
    def tasks(self) -> list[SubAgentTask]
    def cancel(self, task_id: int) -> bool
    def background_now(self, task_id: int) -> bool      # 手动切后台（F8 第三种方式）
```
**委派流程：**
1. 校验：并发已满 → 回灌 `error="后台任务并发已满(N)，请稍后再试"`；defined 且角色不存在 → `error="未知角色: X"`。
2. 前台：`await runner.run_role_agent(...)` → 摘要回灌；`timeout` 到 → 转后台并回占位结果。
3. 后台（含 Fork 强制、显式 background、超时转、手动切）：`create_task` 入后台集合，立即返回 `output=f"已转后台，task_id=N，状态=running"`。
4. 完成/失败：更新 task 状态 + usage；经 `on_task_done(task)` 回调通知（main 接线到 app 回灌主对话）。
**深度控制：** `_depth_ctx`（contextvar，默认 0）。delegate 读当前深度，子 Agent 构造时 `+1`；`tool_filter` 据此决定是否禁 `agent`。
**依赖：** model、runner、agent。（不 import UI——notify/on_task_done 均为注入回调）

### subagents/runner.py
**职责：** 构造独立 Agent 跑到底，收集摘要（F3/F4）。
**对外接口：**
```python
async def run_role_agent(role, prompt, *, depth, shared) -> tuple[str, Usage]   # (摘要, 用量)
async def run_fork_agent(prompt, *, parent_msgs, parent_tools, depth, shared) -> tuple[str, Usage]
```
**定义式（F3）：**
- 空白消息列表 `[{"role":"user","content":trigger}]`（trigger = `请以角色「X」执行：{prompt}`——对齐 `_run_isolated_skill` 的触发消息惯例，空历史必须有一条明确指令）。
- 独立 `Agent`：`provider`（角色 `model` 覆盖时用 `dataclasses.replace` 派生新 provider）、`registry`（共享）、`workspace`（共享）、`env`（共享）、`permission`（**独立实例**：workspace 同主、mode=角色 permission 或继承、human_callback 接主 App）、`context_manager`（**独立实例**：独立 estimator/offload，不 attach_chat 不压缩）、`skill_injector=lambda: [role.body]`（角色正文走注入缝）、`max_rounds=role.max_rounds or 默认`、`hooks=shared_hooks`（共享引擎）、`tool_filter=多层防线闭包`。
- 流式消费 Delta 到 done，拼 `text` 为摘要；收集末次 `usage`。
**Fork 式（F4）：**
- `parent_msgs` 深拷贝为初始消息 + 追加触发消息；`parent_tools` 为父当前可见工具集（快照）作子 Agent 初始全量。
- 强制后台（delegate 层保证），首次请求借相同 history 前缀命中 cache。
- system 用标准构建（无 memory/skill 注入）→ 稳定前缀与主一致，最大化 cache 命中。
**依赖：** agent、providers、context、permissions、protocols。

### subagents/tool.py
**职责：** `agent` 工具（F2）。
```python
class AgentTool(Tool):
    name = "agent"; description = "委派子任务给独立子 Agent（defined 按角色 / fork 继承历史）"
    parameters: mode/role/prompt/background/timeout（JSON Schema）
    system_exempt = True      # 常驻、不受 Skill/角色白名单约束
    side_effect = False       # 委派动作本身不改文件（子 Agent 内部行为受其自身权限约束）
    def __init__(self, manager: SubAgentManager): ...
    async def execute(self, **kwargs) -> ToolResult:
        return await self._manager.delegate(...)   # 统一入口，自身永不抛错
```

## 模块交互

```
[主 Agent 工具列表] ──含──> AgentTool.execute ──> SubAgentManager.delegate
                                                      │
                                  ┌───────────────────┴────────────────────┐
                            前台 await runner                         后台 create_task
                                  │                                         │
                 runner.run_role_agent / run_fork_agent               后台集合(状态/usage)
                      │ 构造独立 Agent                                  │
                      ├─ provider/registry/hooks/fs 共享                  │ 完成
                      ├─ permission/context/msgs 独立                     ▼
                      └─ tool_filter 多层防线                    on_task_done 回调
                                  │                              (main 接线到 app)
                                  ▼                                    │
                            摘要回灌 ToolResult                  系统行 + 摘要进主对话 _msgs
```

**回灌语义（F6）：**
- 前台：`delegate` 返回的 `ToolResult(output=摘要)` 由 agent 工具直接回灌模型——主 Agent 下一轮可见并可继续。
- 后台：`on_task_done` → app 写系统行 `[subagent] #N <role> 完成（tokens=…）` + 把摘要作为一条 `user` 消息追加进主对话 `_msgs`（模型后续可见，不自动触发新一轮）。

**命令接线：**
- `/agents`（LOCAL）：列出任务列表（id/角色/状态/用量）。
- `/bg <task_id>`（LOCAL）：手动切后台（F8 第三种方式）。
- 两者注册进 `commands/builtins.py`，走 state14 命令注册中心。

## 文件组织

```
codepilot/subagents/
├── __init__.py          — 导出 SubAgentManager / AgentRole / AgentTool / RoleError
├── model.py             — AgentRole / SubAgentTask / RoleError
├── loader.py            — 四级发现 + 解析 + 覆盖 + 校验
├── manager.py           — SubAgentManager（委派/后台/深度/并发）
├── runner.py            — run_role_agent / run_fork_agent
├── tool.py              — AgentTool
└── data/                — 内置角色（随包 package-data，参照 skills/data）
    └── architect.md     — 内置示例角色（默认放行 agent 的样板）

tests/subagents/
├── test_loader.py       — 四级覆盖 + 校验全集 + 坏文件隔离
├── test_manager.py      — 并发上限 / 深度上限 / 转后台 / 取消 / 状态追踪
├── test_runner.py       — 定义式/Fork 式独立 Agent 构造（mock provider）
├── test_tool.py         — agent 工具参数分流 / 常驻 / 永不抛错
└── test_filters.py      — 多层防线（白名单/黑名单/agent禁/后台只读/plan 继承）
```

**既有改动：** `agent.py`（`tool_filter` 参数 + `_build_tools` 末尾调用）、`main.py`（装配 SubAgentManager + 注册 AgentTool + 接线 on_task_done）、`ui/app.py`（命令 + 回灌 + 手动切后台绑定）、`commands/builtins.py`（`/agents` `/bg`）、`pyproject.toml`（`package-data` 加 `subagents/data/*.md`）。

## 技术决策

| # | 决策点 | 选择 | 理由 |
|---|---|---|---|
| D1 | 子 Agent 执行引擎 | **复用现有 `Agent` 类**，不新写循环 | N6 一致性：跑到底/权限/工具执行/停止判定与主 Agent 同源；`_run_isolated_skill` 已实证可行；零重复代码 |
| D2 | 多层防线落点 | **新增 `tool_filter` 注入参数**，在 `_build_tools` 末尾（skill 白名单 + plan 过滤之后）调用 | 不碰 state15 白名单路径（N1 向后兼容）；角色防线与 Skill 防线正交叠加 |
| D3 | 角色正文注入 | 走 **`skill_injector` 注入缝**（env 后最显眼位） | 复用 state13/15 机制，不新辟通道（N3）；与 `_run_isolated_skill` 同款手法 |
| D4 | 子 Agent 上下文 | 独立 `permission`（独立实例、模式由角色或继承）与独立 `context_manager`（独立 estimator/offload，**不 attach_chat、不压缩**） | F5 隔离；子 Agent 历史短，压缩无必要，仅需层1 offload + 用量计数 |
| D5 | 子 Agent 权限 ask | `human_callback` **接主 App** 确认框（弹窗可授权） | 子 Agent 非交互但可人机回环；权限模式仍由角色 `permission` 字段控制（设 strict/permissive 可避免弹窗） |
| D6 | Fork 式 system | **标准构建**（无 memory/skill/hook 注入块），历史深拷贝 + 触发消息 | 稳定前缀与主一致 + 相同 history 前缀 → 首次请求命中 cache（F4/N5） |
| D7 | Hook 共享边界 | **共享事件发射**（round/tool_pre/tool_post/stop 在子 Agent 生效），**不共享 volatile 注入块**（hook_blocks 不进子 Agent system） | F5「共享 Hook 引擎」指事件；session 级注入属主对话会话上下文，子 Agent 保持干净 |
| D8 | `agent` 工具在 plan 模式 | `side_effect=False` → **plan 下仍可见**；但子 Agent 继承父 plan 只读（tool_filter 滤掉副作用工具） | spec AC2「任何模式不消失」；委派动作本身不改文件，plan 语义经子 Agent 只读继承保持 |
| D9 | 深度防线 | `_depth_ctx`（contextvar）；可见 `agent` 条件 = `role.can_delegate AND depth < max_depth`；**默认 max_depth=1**（子 Agent 一律禁 agent） | F7 全局禁止 + 角色显式放行；默认最安全，配 max_depth=2 才启用多级委派 |
| D10 | 后台并发上限 | 默认 **4**，超限回灌 `error="后台任务并发已满"` | F10；防资源耗尽（N5） |
| D11 | 前台超时 | **转后台而非终止**（任务已花成本，让它跑完再回灌） | F8/F10 默认语义；超时只切调度形态，不杀任务 |
| D12 | 手动切后台 | 前台委派创建可「转后台」的 task，`/bg <id>` 或 **Ctrl+B** 解除 await 移入后台集合，**子 Agent 协程不中断继续跑**。（前台委派时输入框禁用，`/bg` 不可输入——Ctrl+B 是 TUI 中的可达入口） | F8 第三种方式；不杀死进行中的子 Agent |
| D13 | 后台完成回灌 | 写系统行 + **摘要作为一条 `user` 消息追加进主对话 `_msgs`**，不自动触发新一轮 | F6；模型下一条消息时可见，不打断用户当前操作流 |
| D14 | 子 Agent 达轮次上限 | 视为「尽力完成」：摘要注明「达轮次上限」，非失败 | 跑到底非交互语境，limit 是检查点非 error（与主 Agent F2 语义一致） |
| D15 | 插件层 | **预留发现接口 + 默认空目录**，本阶段不真实支持第三方分发 | 范围控制（YAGNI）；目录顺序固定，后续补插件源即可 |
| D16 | 内置角色 | 内置 `architect` 示例（放行 agent 的样板），走 `package-data` | 开箱即用参照 state15 内置样板惯例；也是 can_delegate 语义的可运行示例 |
| D17 | 角色名册注入（真机验收修正） | system prompt 加 volatile 名册块（`subagent_injector`，同 memory/skill/hook 注入缝）+ agent 工具 description 列角色名 + `role` 参数 enum 动态生成 | 真机场景 1 暴露：模型看不到角色文件，不知道有哪些角色，遂自己用 read/glob 干活而不调 agent 工具。名册让委派可发现 |

## Spec 覆盖

| F | 归属 | N | 归属 |
|---|---|---|---|
| F1 角色模型+四级加载 | loader/model | N1 向后兼容 | D2 + tool_filter 默认 None |
| F2 agent 工具常驻 | tool.py | N2 故障隔离 | manager 异常兜底 + on_task_done |
| F3 定义式 | runner.run_role_agent | N3 低耦合 | manager 注入回调、不 import UI |
| F4 Fork 式 | runner.run_fork_agent + D6 | N4 可单测 | 五测试文件 |
| F5 隔离+共享 | D4/D5/D7 | N5 性能 | D6 cache / D10 并发 |
| F6 结果回灌 | D13 + 前台 ToolResult | N6 一致性 | D1 复用 Agent |
| F7 多层防线 | D2/D8/D9 + tool_filter | | |
| F8 后台管理器 | manager + D11/D12 | | |
| F9 故障隔离/可观测 | manager on_task_done + 日志 | | |
| F10 执行控制 | D9/D10/D11/D12 | | |

全部 F/N 有归属，无遗漏。
