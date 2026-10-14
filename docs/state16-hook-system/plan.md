# state16 Hook 系统 Plan

## 架构概览

新包 `codepilot/hooks/` + 既有接缝复用。Hook 引擎不 import agent/ui/textual；Agent 只感知「engine 回调 + injector 回调」两个不透明 callable（与 state13/15 注入缝同构）。

```
YAML 配置 ──→ loader（加载+集中校验）──→ HookRule 列表
                                              │
                              ┌───────────────┴───────────────┐
                              │ HookEngine（匹配 + 触发 + 执行控制）│
                              └───────────────┬───────────────┘
              事件发射点（8 处）                │ fire(event, fields)
   ┌──────────┬──────────┬─────────┬─────────┤
   agent.py   chat.py    app.py    ctx/mgr   │
  (round/    (user_     (session_ (compact) │
   tool_pre/  message)   start/end)          │
   tool_post/                                 │
   stop)                                      ▼
                                     actions 执行器
                              ┌──────┬──────┬──────┬──────┐
                          command  prompt  http  subagent
                              │      │      │     (占位)
                              │      ▼      │
                              │  HookInjector│
                              │  (volatile 块仓库)
                              │      │      │
                              ▼      ▼      ▼
                         日志 + TUI notify 回调（系统行）
```

**五个模块，职责一句话：**

1. **model**——规则数据结构（HookRule / Condition / HookAction）+ 8 事件常量与字段注册表。纯数据，零依赖。
2. **loader**——读项目/用户两级 `.hooks.yaml`，解析为三要素模型，集中校验（F10 全集），非法即 `HookError` 让启动 fail-fast。
3. **engine**——核心。`fire(event, fields)` 匹配命中规则 → 按声明顺序执行动作；持有 once 标记集、异步任务集；tool_pre 走专用的「可拦截」通道返回拒绝原因。
4. **actions**——四种动作的执行器（command 子进程 / http 请求 / prompt 注入 / subagent 占位日志）。各自故障隔离，异常不外抛。
5. **injector**——prompt 动作的落点：volatile 块仓库（去重、每轮重建、`/clear` 复位），对 Agent 暴露为与 `memory_injector`/`skill_injector` 同形态的回调。

**既有代码改动只有「发射点 + 接线」，不改动既有语义**（详见模块设计各节）。

## 核心数据结构

```python
# hooks/model.py —— 纯数据，frozen

class HookError(Exception): ...          # 加载/校验错误，启动 fail-fast

EVENTS = ("session_start", "session_end", "round_start", "user_message",
          "tool_pre", "tool_post", "compact", "stop")

@dataclass(frozen=True)
class CondItem:
    field: str        # 事件字段名（如 tool / command / text / round）
    pattern: str      # 共享匹配语法的 pattern

@dataclass(frozen=True)
class Condition:
    mode: str                       # "all" | "any"
    items: tuple[CondItem, ...]     # 至少一条

@dataclass(frozen=True)
class HookAction:
    type: str                       # command | prompt | http | subagent
    fields: dict                    # 该动作的原始字段（cmd/text/url/...）
    timeout: float | None           # 执行控制三选项（action 级）
    is_async: bool
    once: bool

@dataclass(frozen=True)
class HookRule:
    event: str                      # 8 事件之一
    condition: Condition | None     # None = 无条件
    action: HookAction
    source: str                     # 来源文件路径（日志/排错用）
    ordinal: int                    # 文件内序号（once 标记、声明顺序的键）
```

```python
# hooks/engine.py —— 对外接口

class HookEngine:
    def __init__(self, rules: list[HookRule], injector: HookInjector,
                 notify: Callable[[str], None] | None = None) -> None: ...
        # notify: TUI 系统行回调，app 创建后接线（同 permission.human_callback 模式）

    async def fire(self, event: str, fields: dict) -> None:
        """非拦截事件入口。零规则短路（N5）。命中规则按声明顺序执行，
        once 跳过已发、async 进后台任务、失败记日志+notify，绝不外抛。"""

    async def fire_pre_tool(self, tool: str, arguments: dict) -> str | None:
        """tool_pre 专用可拦截通道：同步执行命中规则的 command 动作，
        首个非零退出 → 返回拒绝原因（stderr）；异常/超时 → 记日志放行（决策B）。
        全部放行/无命中 → None。"""

    async def aclose(self) -> None:
        """session_end/退出时：等后台任务收尾（有界等待），取消未完成的。"""
```

```python
# hooks/injector.py —— prompt 动作落点

class HookInjector:
    def add(self, key: str, text: str) -> None   # key=规则标识，同 key 覆盖（去重）
    def hook_blocks(self) -> list[str]           # Agent 每轮回调；空规则 → []
    def clear(self) -> None                      # /clear 联动
```

### 共享匹配器升级（`permissions/rules.py`）

```python
def match_text(pattern: str, subject: str | None) -> bool:
    # 1. `!` 前缀 → 反向（可包任意下层语法：!git * / !/rm -rf/ / !exact）
    # 2. `/.../` 首尾包 → re.search（加载期已编译校验，运行时防御性 no-match）
    # 3. 含 * ? [ → fnmatch（现状不变）
    # 4. 否则精确相等（现状不变）
```

## 模块设计

### hooks/model.py
**职责：** 规则三要素的不可变数据模型 + 事件/动作常量 + 事件字段注册表（校验用）。
**对外接口：** `EVENTS`、`ACTION_TYPES`、`EVENT_FIELDS: dict[str, set[str]]`（保留字段；tool 事件的参数键放开不校验）、上述 dataclass。
**依赖：** 仅 stdlib。

### hooks/loader.py
**职责：** 两级 YAML 加载 + 解析 + 集中校验。
**对外接口：** `load_hooks(project_path: Path, user_path: Path) -> list[HookRule]`——文件缺失/空忽略；`hooks:` 顶层键列表逐条解析；顺序为用户级在前、项目级在后。
**校验全集（F10，违反即 HookError）：** 未知事件、未知 action type、`all`/`any` 混用或同现、条件条目空、正则编译失败、command 缺 `cmd`、http 缺 `url`、prompt 缺 `text`、tool_pre 配 `async: true`、非 tool 事件的条件字段不在该事件注册表。
**依赖：** model、permissions.rules（正则预编译校验复用 match_text 语法判定）。

### hooks/engine.py
**职责：** 规则匹配 + 触发 + 执行控制（once/async/timeout）+ 拦截通道 + 后台任务管理。
**对外接口：** 见核心数据结构。
**关键行为：**
- 入口统一把 fields 值 `str()` 化（bool→"true"/"false"，int→十进制）。
- `fire`：按 `event` 过滤 → 条件判定（Condition 语义见下）→ 命中规则按声明顺序执行；`once` 命中过（键 `(source, ordinal)`）跳过；`is_async` → `asyncio.create_task` 入后台任务集（done 回调记日志+notify），否则 await。
- `fire_pre_tool`：只处理 event=="tool_pre" 且 action.type=="command" 的命中规则，同步逐个执行；首个非零退出返回 stderr（截断）；异常/超时记日志继续（fail-open）；prompt/http/subagent 动作在 tool_pre 上同样执行但不影响拦截判定。
- 任何动作异常不外抛（N2）。
**依赖：** model、actions、injector、permissions.rules.match_text。

**条件判定语义：** `Condition.mode=="all"` → 全部 CondItem `match_text(pattern, fields.get(field))` 为真；`"any"` → 任一为真；`condition is None` → 恒真。

### hooks/actions.py
**职责：** 四动作执行器，各自故障隔离。
**对外接口（均 async，返回 (ok: bool, detail: str)；command 额外返回 exit_code/stdout/stderr 摘要）：**
- `run_command(cmd, fields, timeout, env_extra)` — `asyncio.create_subprocess_shell`；环境变量 `CODEPILOT_HOOK_EVENT`/`CODEPILOT_TOOL`/`CODEPILOT_TOOL_ARGS`(JSON)；`{field}` 占位符渲染（缺字段保留原样不炸）。
- `run_http(url, method, headers, body, timeout)` — stdlib `urllib` + `asyncio.to_thread`；响应只记日志。
- `run_prompt(text, fields, injector, key)` — 渲染占位符 → `injector.add`。
- `run_subagent(...)` — 记日志「subagent 动作未实现」跳过。
**依赖：** injector（仅 prompt）、stdlib。

### hooks/injector.py
**职责：** prompt 动作的 volatile 块仓库。
**对外接口：** 见核心数据结构。`hook_blocks()` 稳定顺序（插入序）；空 → `[]`。
**依赖：** 仅 stdlib。

### permissions/rules.py（改）
**职责：** `match_text` 升级四语法；`parse_rule`/`merge` 不动。
**兼容：** 旧 pattern（无 `!` 前缀、非 `/.../` 包裹）走原路径，行为一字不变。

### prompt/builder.py（改）
**职责：** `build_system_prompt` 新增可选 `hook_blocks: Sequence[str] = ()`，位置紧跟 `skill_blocks` 之后（env 后显眼区）。

### agent.py（改）
**职责：** 新增可选参数 `hooks`（engine）、`hook_injector`（callable）；4 个发射点：
- `round_start`：每轮循环顶（round Delta 之后），fields={round}。
- `tool_pre`：`_execute_one` 权限 check 通过之后、execute 之前；返回 reason → `ToolResult(error=f"Hook 拦截: {reason}")`。
- `tool_post`：`_execute_one` 拿到结果后，fields 含 ok。
- `stop`：done/limit/stall/unknown_tool 四个终止出口收拢发射。
- `build_system_prompt(..., hook_blocks=self._hook_injector() ...)`。
**零 hooks 时全部短路，行为与现状一致。**

### chat.py（改）
**职责：** 新增可选参数 `hooks`；`send_message` 非 resume 时，`_msgs` 追加后、agent.run 前 `await hooks.fire("user_message", {"text": content})`。

### context/manager.py（改）
**职责：** 新增可选属性 `on_compact: Callable[[], Awaitable[None]] | None`；`_do_compact` 成功末尾 await 之。不 import hooks。

### ui/app.py（改）
**职责：**
- `session_start`：on_mount 末尾 `run_worker(engine.fire("session_start", {"resumed": ...}))`。
- `session_end`：on_unmount best-effort fire + `engine.aclose()` 收尾。
- `engine.notify = self._hook_notify`（系统行写 RichLog，形如 `[hook] ...`）。
- `/clear` 联动：`_clear_display` 追加 `hook_injector.clear()`。
- ChatManager 构造传 `hooks=engine`。

### main.py（改）
**职责：** 装配——`load_hooks(workspace/".hooks.yaml", ~/.hooks.yaml)` → injector → engine(notify=None) → Agent(hooks=, hook_injector=) → app 建后 notify/chat 接线 → context_manager.on_compact 接线。`HookError` → 打印错误退出（同 ConfigError 处理）。

## 模块交互

**8 个发射点：**

| 事件 | 发射位置 | fields | 通道 |
|---|---|---|---|
| `session_start` | `app.on_mount` 末尾 | `resumed`: "true"/"false" | `run_worker(engine.fire(...))` |
| `session_end` | `app.on_unmount` | 无 | best-effort fire + `engine.aclose()` |
| `round_start` | `agent.run` 每轮循环顶 | `round`: str(int) | `await engine.fire(...)` |
| `user_message` | `chat.send_message`（非 resume） | `text` | `await engine.fire(...)` |
| `tool_pre` | `agent._execute_one` 权限通过后 | `tool` + 参数摊平 | `reason = await engine.fire_pre_tool(...)` → 拦截回灌 |
| `tool_post` | `agent._execute_one` 结果后 | `tool` + 参数 + `ok` | `await engine.fire(...)` |
| `compact` | `context/manager._do_compact` 成功末尾 | 无 | `on_compact` 回调（main 接线） |
| `stop` | `agent.run` 终止出口收拢处 | `reason`(done/limit/stall/unknown_tool) | `await engine.fire(...)` |

**字段类型约定**：所有 fields 值在 engine 入口统一 `str()` 化，匹配永远是字符串语义。

**装配顺序（main.py）**：load_hooks（用户级前、项目级后）→ HookInjector → HookEngine(rules, injector, notify=None) → Agent(hooks=engine, hook_injector=injector.hook_blocks) → app 创建后 `engine.notify=...`、ChatManager(hooks=engine) → context_manager.on_compact 接线。

## 文件组织

```
codepilot/
├── hooks/
│   ├── __init__.py      — 导出 HookEngine / HookInjector / load_hooks / HookError
│   ├── model.py         — HookRule/Condition/CondItem/HookAction/HookError/EVENTS/事件字段注册表
│   ├── loader.py        — 两级 YAML 加载 + 集中校验（F10 全集）
│   ├── engine.py        — HookEngine：匹配/fire/fire_pre_tool/once/async/timeout/aclose
│   ├── actions.py       — 四动作执行器（command/http/prompt/subagent）
│   └── injector.py      — HookInjector：prompt 动作的 volatile 块仓库
├── permissions/rules.py — match_text 升级四语法（改）
├── prompt/builder.py    — build_system_prompt 加 hook_blocks 参数（改，紧跟 skill_blocks 后）
├── agent.py             — 4 发射点 + hooks/hook_injector 可选参数（改）
├── chat.py              — user_message 发射点 + hooks 可选参数（改）
├── context/manager.py   — on_compact 可选回调（改）
├── ui/app.py            — session_start/end 发射点 + notify 接线 + /clear 联动（改）
├── main.py              — 装配（改）
└── tests/hooks/
    ├── test_match.py        — 四语法 + 反向包裹 + 权限既有行为回归
    ├── test_loader.py       — 两级加载 + F10 校验全集
    ├── test_engine.py       — 匹配/fire/once/async/timeout/声明顺序
    ├── test_intercept.py    — tool_pre 拦截/放行/fail-open/权限先于 Hook
    ├── test_actions.py      — command 退出码与 stderr/http/subagent 占位
    └── test_injection.py    — prompt 注入 volatile 块/去重/clear 复位
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 拦截表达 | tool_pre 上 command 退出码非 0 = 拦截，stderr 为原因 | 不引第五种动作；声明式拒绝归权限系统，Hook 定位可编程策略（spec 决策 A） |
| tool_pre 异常/超时 | 记日志放行（fail-open） | hook 脚本 bug 不应锁死所有工具（spec 决策 B） |
| 匹配器升级落点 | 改 `permissions/rules.match_text` 一处，Hook 条件直接复用 | 真正的「一套语法」；权限规则顺带获得反向/正则，既有规则行为不变 |
| `/regex/` 判定 | 首尾均为 `/` 且长度 ≥2 → `re.search` | 与 glob 路径 `/etc/*` 不冲突（不以 `/` 结尾）；`/etc/` 这类极端 pattern 语义变为正则，属可接受边界，文档注明 |
| 正则校验时机 | hooks loader 加载期编译校验（fail-fast）；match_text 运行时对非法正则防御性 no-match | N1 要求配置错误启动暴露；权限规则路径不新增抛错行为（向后兼容） |
| Hook 包对 Agent/UI 的依赖 | 零 import；Agent 感知两个不透明 callable，UI 经 notify 回调 | 与 state13/15 注入缝同构，N3 |
| tool_pre 发射位置 | 权限 check 通过**之后** | 权限管「能不能跑」，Hook 叠加可编程策略；权限拒绝的工具不消耗 Hook（spec F2 注） |
| 多命中执行顺序 | 声明顺序；两级合并为**用户级先、项目级后** | 无优先级字段（spec 不做的事）；项目级更具体、靠后声明，与「项目覆盖用户」心智同向（叠加语义下顺序即语义） |
| HTTP 实现 | stdlib `urllib` + `asyncio.to_thread` | providers 用各自 SDK、项目无统一异步 HTTP 客户端；不引新依赖，通知型请求 to_thread 足够 |
| 事件字段类型 | engine 入口统一 `str()` 化 | 匹配器是字符串语义；bool→"true"/"false"、int→十进制，规则作者预期稳定 |
| once 键 | `(source, ordinal)` | 规则无名字段；来源文件+序号天然唯一，热重载不做（spec 不做的事） |
| session_end 时序 | best-effort fire + `engine.aclose()` 有界等待 | on_unmount 处于关停流程，异步动作可能被取消——记日志兜底，不保证 command 跑完（写进文档） |
| 配置路径 | 固定 `<workspace>/.hooks.yaml` 与 `~/.hooks.yaml`，不进 config.yaml | 与 Skill 的 `.skills` 目录先例一致；少一个配置维度 |
| 占位符渲染 | `str.format_map` + 缺字段容错（保留原样不炸） | N2 故障隔离：渲染失败不中断主流程 |
| spec 笔误修正 | 「9 事件」→「8 事件」 | spec F2 表实为 8 行；落盘 plan 时同步修 spec.md 与 AC2 措辞 |
