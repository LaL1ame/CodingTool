# state14 命令注册与分发机制 Plan

## 架构概览

把 `codepilot/commands.py`（单文件硬编码 if-链）升级为 `codepilot/commands/` 子包，五个职责各居一层，全部**不依赖 Textual、可单测**；UI 只做「分流 + 实现界面控制接口」。

```
┌──────────────────────────────────────────────────────────────────┐
│ UI 层(ui/app.py)                                                   │
│   _submit: 分流器(命令→本地分发 / 非命令→AI) + 补全菜单 + CommandContextImpl │
│   PromptInput: tab 补全  ·  StatusBar: [DEFAULT]/[PLAN] 标记        │
└─────────────┬──────────────────────────────┬──────────────────────┘
              │ 解析+分发                      │ 实现界面控制接口(DIP)
      ┌───────▼────────┐  返回 str|None(→AI)  ┌▼──────────────────────┐
      │ commands/      │ ───────────────────► │ CommandContext(协议)  │
      │  registry.py   │   handler(ctx,param)│   show/submit/开关/查询  │
      │  parser.py     │                      └──────────────────────┘
      │  dispatcher.py │  handler 只认 ctx
      │  builtins.py   │
      │  completer.py  │
      └────────────────┘
```

- **解析+分发层**（`commands/`）：纯逻辑，注册中心 → 解析器 → 分发器 → 内置命令 → 补全候选。只依赖一个 `CommandContext` **协议**。
- **界面控制接口**（`CommandContext`）：命令实现触达 UI 的**唯一通道**。当前 `CodePilotApp` 提供实现；以后换渲染框架另实现一套即可，命令零改动（满足 F4）。
- **UI 层**：`_submit` 收窄为分流器；`PromptInput` 接 Tab 补全；补全多命中弹菜单；状态栏模式标记改为 `[DEFAULT]/[PLAN]`。

## 核心数据结构

### `CommandKind`（枚举）
```python
class CommandKind(Enum):
    LOCAL = "local"    # 纯本地：不进对话、不改 UI 状态机（/help /exit /memory /session /status）
    UI    = "ui"       # 影响界面/应用状态机（/clear /plan /do /mode /edits /compact /continue /forget /permission /auto|manual|strict）
    PROMPT = "prompt"  # 把预设提示词送对话交给 AI（/review）
```

### `CommandSpec`（数据类）
```python
@dataclass(frozen=True)
class CommandSpec:
    name: str                       # 规范名(小写, 无前导斜杠)
    kind: CommandKind
    desc: str                       # 简短描述(供 /help)
    handler: Callable[[CommandContext, str], str | None]  # (ctx, params)-> 转发消息|None
    aliases: tuple[str, ...] = ()   # 别名(无前导斜杠)
    usage: str = ""                 # 用法示例, 如 "/mode <manual|auto|strict>"
    arg_hint: str | None = None     # 参数提示(供 /help/补全)
    hidden: bool = False            # 隐藏命令不参与补全、不出现在 /help
```
处理函数契约即 spec F3：返回一条用户消息 → 分发器转交 AI；返回 `None` → 已本地处理完。

### `CommandRegistry`
```python
class CommandRegistry:
    def register(self, spec: CommandSpec) -> None:
        # 立即查重：name/任何 alias 与已注册者撞名 → 抛 CommandConflictError(含双方名)
    def resolve(self, token: str) -> CommandSpec | None:
        # token 小写后 精确匹配 name 或任一 alias(不补模糊)
    def all(self, *, include_hidden: bool = True) -> list[CommandSpec]:
        # 按 name 排序返回
    def candidates(self, prefix: str) -> list[str]:
        # 补全候选：匹配 name+alias, 排除 hidden, 去重, 排序(供 completer)
    def assert_no_conflicts(self) -> None:
        # 全量两两校验(含别名互撞), 冲突抛异常 —— 启动期调用
```

### `ParseResult`（数据类）
```python
@dataclass
class ParseResult:
    kind: Literal["blank", "message", "command", "unknown"]
    spec: CommandSpec | None = None
    params: str = ""
    text: str = ""        # 原始输入(供镸整/提示)
```
- `blank`：空/全空白（早返回）
- `message`：非斜杠开头普通输入 → 交 AI
- `command`：命中命令，携 spec + params
- `unknown`：斜杠开头但未命中 → 给 `/help` 引导，**不送 AI**（方案 A）

### `CommandContext`（Protocol，界面控制接口 / 命令触达 UI 唯一通道）
命令实现只依赖本协议，不 import App/Textual。
```python
class CommandContext(Protocol):
    def show(self, message: str) -> None: ...          # 写一行消息到会话展示区
    def submit_to_ai(self, text: str) -> None: ...     # 把一条用户消息送进 AI 循环
    def set_axis(self, axis: ToolAxis, *, announce: bool = False) -> None: ...
    def set_release(self, policy: ReleasePolicy) -> None: ...
    def set_accept_edits(self, on: bool | None = None) -> None: ...
    def compact(self) -> None: ...                     # 触发异步压缩(内部 run_worker)
    def resume_loop(self) -> None: ...                 # /continue 编排(内部 run_worker 续跑)
    def delete_memory(self, keyword: str) -> int: ...  # 删记忆条目, 返回删除数
    def memory_report(self) -> str | None: ...         # /memory 展示块(或 None=未启用)
    def clear_display(self) -> None: ...               # 清屏显示(保留历史/上下文)
    def snapshot(self) -> StatusSnapshot: ...          # 状态快照(/status /permission /session 查询)
    def exit_app(self) -> None: ...
```

### `StatusSnapshot`（数据类，供查询命令）
```python
@dataclass
class StatusSnapshot:
    axis_label: str          # "DEFAULT" | "PLAN"（展示标记）
    release: str             # manual | auto | strict
    accept_edits: bool
    model: str
    tokens_in: int
    tokens_out: int
    ctx_usage: str
    elapsed_s: float
    session_file: str | None
```
`/status` 全量展示；`/permission` 取 axis_label/release/accept_edits；`/session` 取 session_file（再 `store.summarize` 补消息数/时长）。

## 模块设计

### `commands/registry.py`
**职责：** 命令注册中心 + 冲突检测（F1/N1）。
**对外接口：** `CommandSpec`、`CommandKind`、`CommandRegistry`、`CommandConflictError`。
**依赖：** 无（纯集合操作）。
`register()` 对 name 与每个 alias 即时查重；`assert_no_conflicts()` 做全量两两校验兜底（捕获「同名别名错位」与「别名互撞」）。启动装配处调用 `assert_no_conflicts()`，冲突即 `sys.exit(1)`。

### `commands/parser.py`
**职责：** 斜杠前缀解析（F2）。
**对外接口：** `parse_command(text: str, registry: CommandRegistry) -> ParseResult`。
**依赖：** registry（做命中判定）。
首空白前为命令名、余下为参数去首尾空白；小写不敏感；全空白 → `blank`；非斜杠 → `message`；斜杠+命中 → `command`；斜杠+未命中 → `unknown`。空/裸 `/` 归 `unknown`。

### `commands/dispatcher.py`
**职责：** 把命中命令交给处理函数（F3/F4）。
**对外接口：** `dispatch_command(spec, ctx, params) -> str | None`。
**依赖：** registry（取 handler）、context（协议）。
调用 `spec.handler(ctx, params)`；若处理函数是协程（N4），返回其协程由调用方 `run_worker` 执行；否则直接返回其 `str | None`。

### `commands/context.py`
**职责：** 定义 `CommandContext` 协议与 `StatusSnapshot`（F4）。
**对外接口：** `CommandContext`（Protocol）、`StatusSnapshot`。
**依赖：** modes（ToolAxis/ReleasePolicy 类型引用，仅作类型标注）。
只是一个抽象层，无实现——实现由 `ui/app.py` 提供。

### `commands/builtins.py`
**职责：** 内置命令全集定义 + 装配（F7）。
**对外接口：** `build_registry() -> CommandRegistry`、`REVIEW_PROMPT`（预设提示词常量）。
**依赖：** registry、context、dispatcher、`codepilot.session.store`（/session 用）、`codepilot.memory`（/memory 展示）。
逐条登记 18 条命令（spec 中的表），全部 sync handler；用 `CommandContext` 的语义操作实现。`/plan`/`/do` 带消息 → `ctx.set_axis(...)` 后返回 params（转发）；`/compact`/`/continue` → 调 `ctx.compact()/ctx.resume_loop()` 返回 None（编排在 ctx 内 run_worker，至此同步）。

### `commands/completer.py`
**职责：** Tab 补全候选（F6）。
**对外接口：** `make_completer(registry) -> Callable[[str], list[str]]`。
**依赖：** registry（`candidates`）。
输入即当前行文本；返回匹配前缀的补全候选（规范命令名）。派发给 `PromptInput`；隐藏命令不出现。

### `codepilot/ui/app.py`（改）
**职责：** 分流器 + `CommandContextImpl` + 补全接线 + 模式标记（F5）。
- 删 `parse_command` 硬编码链 → `_submit` 收窄为分流器：
  ```
  pr = parse_command(text, registry)
  blank → return
  message → self._start_ai(pr.text)
  unknown → ctx.show("/help 引导……")
  command → out = dispatch_command(pr.spec, ctx, pr.params)
            out 为串 → self._start_ai(out)   # 转发
            为协程 → self.run_worker(await→start_ai)
  ```
- 抽出 `_start_ai(text)`：现有「清 prompt/写 You/置 streaming/set_interval/run_worker(_process_chat)」的 AI 启动封装。
- 提供 `CommandContextImpl`（组合 App）实现 `CommandContext`——把各 `_cmd_*`、`_set_axis`、`_process_chat`、`_refresh_status` 收敛为协议方法。
- `_refresh_status` 里 `axis.value` 翻译成标记：`build→"DEFAULT"`、`plan→"PLAN"`，传给 StatusBar（满足 F4 状态栏联动）。
- `on_prompt_input_submitted` → 装配 registry/completer/ctx（构造注入）。

### `codepilot/ui/widgets/prompt_input.py`（改）
**职责：** Tab 补全触发（F6）。
- 构造增可选 `completer: Callable[[str], list[str]] | None = None`。
- `_on_key` 增 `tab` 分支：若光标前文本以 `/` 开头，取 `completer(line)`：
  - 1 个候选 → 直接扩为 `/<名>`（+可选空格）替换该段；
  - 多个候选 → `post_message(CompletionRequested(candidates, prefix))` 交 App 弹菜单；
  - 0 个 → 忽略（保持输入）。
- 发送/换行逻辑（enter/ctrl+j）不变。

### `codepilot/ui/screens.py`（改，追加）
**职责：** 补全弹菜单（多命中）。
- 新 `CompletionScreen(candidates, on_pick)`：Textual Screen，`ListView` 列候选；上/下移动、Enter 选取（把 `/<选中名> ` 填入 PromptInput）、Esc 关闭。隐藏命令已由 completer 剔除，这里不参与。

### `codepilot/ui/widgets/status_bar.py`（可选，改）
**职责：** 模式标记展示。
不改核心；若把 `DEFAULT/PLAN` 标签映射放这里更内聚也可（由 App `_refresh_status` 传入已翻译的 axis_label）。倾向放在 App 内，保持 StatusBar 无映射逻辑。

## 模块交互

**分流（`_submit`，每回车）**
```
on_prompt_input_submitted
  └ _submit(text)
      ├ parse_command(text, registry) → ParseResult
      │    blank → return
      │    message → _start_ai(text)                      # 唯一 AI 入口
      │    unknown → ctx.show("/help 引导")
      │    command → dispatch_command(spec, ctx, params)
      │         ├ out=None → 本地已处理
      │         ├ out=str → _start_ai(out)                # plan/do 带消息 / review
      │         └ out=coro → run_worker(_finish(out))     # 未来 async handler
```
**补全（Tab）**
```
PromptInput._on_key(tab) → completer(line)
  1 命中 → 直接扩写
  N 命中 → post CompletionRequested → App → push_screen(CompletionScreen)
            选取 → 填回 PromptInput, close
```
**查询命令**
```
/status → ctx.snapshot() → 分组显示
/session → ctx.snapshot().session_file → store.summarize → 显示
/permission → ctx.snapshot()(axis/release/ae) → 显示
/help → registry.all() → 按 kind 分组列出 name/usage/desc
```

## 文件组织
```
codepilot/
  commands.py                 ← 删除(升级为包; 避免文件/包同名冲突)
  commands/
    __init__.py               # 导出 CommandSpec/CommandKind/CommandRegistry/parse_command/
                              #   dispatch_command/CommandContext/build_registry/REVIEW_PROMPT/make_completer
    registry.py               # CommandSpec, CommandKind, CommandRegistry, CommandConflictError
    parser.py                 # parse_command
    dispatcher.py             # dispatch_command
    context.py                # CommandContext(Protocol), StatusSnapshot
    builtins.py               # build_registry(), REVIEW_PROMPT, 18 条命令 handler
    completer.py              # make_completer
  ui/
    app.py                    # 分流器重构 + CommandContextImpl + 标记 DEFAULT/PLAN + 补全接线
    widgets/prompt_input.py   # +tab 补全(completer 注入)
    widgets/status_bar.py     # (可选) 标记映射
    screens.py                # +CompletionScreen(补全弹菜单)
tests/
  test_commands.py            # 改: 适配 parse_command 新签名
  test_commands_parser.py     # F2: blank/message/command/unknown + 大小写
  test_commands_registry.py   # F1: 注册/冲突/别名/候选
  test_commands_builtins.py   # F7: 各命令 handler 行为(用假 CommandContext)
  test_commands_dispatcher.py # F3: 返回 str→转发 / None→本地 / 协程分支
  test_commands_completer.py  # F6: 单命中/多命中/隐藏排除
  test_commands_context.py    # F4: 假实现可驱动全部命令(换渲染框架复用)
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 命令存储形态 | 单文件 → 子包 `commands/` | 需删 `commands.py`(Windows 不容文件/包同名)；五个职责分文件可单测、低耦合 |
| 处理函数契约 | `handler(ctx, params) -> str\|None` | 统一「转发 vs 本地」；`plan/do 带消息`与`/review`走同一回路(F3) |
| 冲突检测时机 | `register()` 即时查重 + `assert_no_conflicts()` 启动兜底 | 双保险；启动即失败退出(N1)，不等运行时炸 |
| 命令与 UI 解耦 | `CommandContext` 协议(DIP) | 命令只认协议；换渲染框架另实现即可(F4) |
| 未命中处理 | `unknown` → 给 `/help` 引导，**不送 AI**(方案A) | 行为确定，符合「非对话杂事不走 LLM」 |
| 模式标记 | `build→DEFAULT / plan→PLAN`，App 内翻译 | BUILD 轴即「执行模式」→ `[DEFAULT]`，展示对齐用户语义(F7) |
| compact/continue 取向 | sync handler 内调 `ctx.compact()/resume_loop()`(内部 run_worker) | 编排下沉到 ctx，handler 保持同步、可测；N4 仍支持协程 handler |
| 补全多命中 | `CompletionScreen` 弹菜单(ListView) | 满足 F6「多命中弹菜单」；单命中直接扩 |
| 内置命令数 | 18 条(现有+6 新) | 「十个高频」指用户命名集合，现有命令一并迁入统一承载；实际 18 条 |
| 配置 | 无需新增配置段 | 命令静态内置，YAGNI |

**接口完整性自检**：`build_system_prompt` 不涉及；`parse_command` 仍导出（签名变化，删旧 if-链）；`_submit` 语义保持（命令消费不进对话、`plan/do 带消息`落回、`--new`/resume 不受影响）。`commands/` 不 import ui/prompt，只依赖 `context`(协议) + `registry`(集合) + `session.store`/`memory`(领域模块)。