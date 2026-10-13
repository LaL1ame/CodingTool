# state14 命令注册与分发机制 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 删除+新建 | `codepilot/commands.py` → `codepilot/commands/` | 旧 if-链升级为子包(Windows 文件/包不可同名,须一步完成) |
| 新建 | `codepilot/commands/context.py` | `CommandContext`(协议) + `StatusSnapshot` |
| 新建 | `codepilot/commands/registry.py` | `CommandSpec`/`CommandKind`/`CommandRegistry`/冲突 |
| 新建 | `codepilot/commands/parser.py` | `parse_command` |
| 新建 | `codepilot/commands/dispatcher.py` | `dispatch_command` |
| 新建 | `codepilot/commands/builtins.py` | 18 条命令 + `REVIEW_PROMPT` + `build_registry` |
| 新建 | `codepilot/commands/completer.py` | `make_completer` |
| 新建 | `codepilot/commands/__init__.py` | 子包导出 |
| 修改 | `codepilot/ui/app.py` | 分流器重构 + `CommandContextImpl` + `[DEFAULT]/[PLAN]` + 补全接线 |
| 修改 | `codepilot/ui/widgets/prompt_input.py` | Tab 补全触发 |
| 修改 | `codepilot/ui/screens.py` | +`CompletionScreen`(补全弹菜单) |
| 改 | `tests/test_commands.py` | 适配新 `parse_command` 签名 |
| 新建 | `tests/test_commands_parser.py` | F2 |
| 新建 | `tests/test_commands_registry.py` | F1 |
| 新建 | `tests/test_commands_builtins.py` | F7(用假 context) |
| 新建 | `tests/test_commands_dispatcher.py` | F3 |
| 新建 | `tests/test_commands_completer.py` | F6 |
| 新建 | `tests/test_commands_context.py` | F4:假实现可驱动全部命令 |

## 执行顺序

```
T1(包骨架+context) → T2(registry) → T3(parser) → T4(dispatcher) → T5(completer)
        ↘ T2 ────────────── T6(builtins, 依赖 T1/T2/T4) → T7(__init__ 导出)
T6 → T8(ui/app 分流器+接线) → T9(补全 UI) → T10(测试适配+全量回归)
```

---

## T1: 包骨架 + CommandContext 协议

**文件：** `codepilot/commands/__init__.py`(暂空)、`codepilot/commands/context.py`；**删除** `codepilot/commands.py`
**依赖：** 无（必须直接执行——因 Windows 文件/包不可同名，删除与建包同一原子步）
**步骤：**
1. 删除 `codepilot/commands.py`（旧硬编码 if-链）。
2. 新建 `commands/` 目录与 `__init__.py`（暂空占位）。
3. 在 `context.py` 定义 `StatusSnapshot`（数据类：axis_label, release, accept_edits, model, tokens_in, tokens_out, ctx_usage, elapsed_s, session_file）。（`CommandKind` 归 `registry.py`，见 T2。）
4. 定义 `CommandContext`（`typing.Protocol`）：show / submit_to_ai / set_axis / set_release / set_accept_edits / compact / resume_loop / delete_memory / memory_report / clear_display / snapshot / exit_app。set_axis/set_release 的 `ToolAxis`/`ReleasePolicy` 类型标注 import `codepilot.modes`（仅类型,不下沉依赖）。
5. `set_axis(axis: ToolAxis, *, announce: bool)`、`set_release(policy: ReleasePolicy)` 的类型标注 import `codepilot.modes`（仅类型,不下沉依赖）。

**注意：** 此步会暂时让 `app.py` 的 `from codepilot.commands import parse_command` 断掉——T8 才切换。本步与后续直到 T8 的运行验证**只跑 targeted 测试**（`tests/test_commands_*.py`），不跑全量。

**验证：** `python -c "from codepilot.commands.context import CommandContext, StatusSnapshot"` 成功；`from codepilot.commands import context` 成功。

---

## T2: 命令注册中心

**文件：** `codepilot/commands/registry.py`、`tests/test_commands_registry.py`
**依赖：** T1（CommandKind 不强制依赖 context,但枚举归属可放 registry；此处 `CommandKind` 放在 registry.py 亦可。以 plan 为准——枚举归 `context.py` 或 `registry.py` 均可，选 registry.py 就近）
**步骤：**
1. `@dataclass(frozen=True) CommandSpec`: name, kind, desc, handler, aliases=(), usage="", arg_hint=None, hidden=False。
2. `CommandKind` 枚举（LOCAL/UI/PROMPT）。
3. `CommandConflictError(Exception)`。
4. `CommandRegistry.register(spec)`：对 name 与每个 alias 与已注册者的 name+aliases 查重，撞 → 抛 `CommandConflictError`（消息含两方命令名与撞的 token）。
5. `resolve(token)`：token 小写后精确匹配 name 或 alias，返回 spec 或 None。
6. `all(include_hidden=True)`：按 name 排序。
7. `candidates(prefix)`：返回匹配前缀的 name+alias（小写、去重、排除 hidden、排序）。
8. `assert_no_conflicts()`：全量两两校验（覆盖 alias-vs-alias），冲突抛 `CommandConflictError`。

**验证：** `pytest tests/test_commands_registry.py -v` 绿——断言：重复注册抛错、别名撞名抛错、resolve 大小写不敏感命中别名、`assert_no_conflicts` 检出不直接 register 撞的口，candidates 排除 hidden 且去重。

---

## T3: 解析器

**文件：** `codepilot/commands/parser.py`、`tests/test_commands_parser.py`
**依赖：** T2
**步骤：**
1. `@dataclass ParseResult`: kind(Literal["blank","message","command","unknown"]), spec=None, params="", text=""。
2. `parse_command(text, registry) -> ParseResult`：
   - `text.strip()` 为空 → `blank`。
   - 不以 `/` 开头 → `message`（text 存原文）。
   - 以 `/` 开头 → 首空白前为 token、余为 params.strip()；`registry.resolve(token.lower())` 命中 → `command`（携 spec+params）；未命中或 token 仅 `/` → `unknown`。

**验证：** `pytest tests/test_commands_parser.py -v` 绿——断言 blank/message/command/unknown 四态、`/CLEAR`≡`/clear`、`/forget 词` → params="词"、`/`裸斜杠 → unknown、（`/`+未命中）→ unknown 且 spec=None。

---

## T4: 分发器

**文件：** `codepilot/commands/dispatcher.py`、`tests/test_commands_dispatcher.py`
**依赖：** T1（ctx 协议）、T2
**步骤：**
1. `def dispatch_command(spec, ctx, params) -> str | None`：
   - 取 `spec.handler`；`functools.partial` 不必。
   - `inspect.iscoroutinefunction(handler)` → 返回 `handler(ctx, params)`（协程，调用方 run_worker）；否则直接调用，返回其 `str | None`（N4 兼容）。

**验证：** `pytest tests/test_commands_dispatcher.py -v` 绿——假 spec+假 handler：返回 str 时上层拿到转发的串；返回 None 时本地；协程 handler 时返回 awaitable（`inspect.isawaitable` 为真）。

---

## T5: 补全候选

**文件：** `codepilot/commands/completer.py`、`tests/test_commands_completer.py`
**依赖：** T2
**步骤：**
1. `def make_completer(registry) -> Callable[[str], list[str]]`：返回闭包，输入整行文本，取当前命令前缀（行首 `/` 后到光标即调用方传入的最左 token），委托 `registry.candidates(prefix)`，返回候选命令名列表。

**验证：** `pytest tests/test_commands_completer.py -v` 绿——挂假 registry：`/` + 全量候选、`/p` 只含 plan/permission、隐藏命令不出现在补全、去重（alias 不再重复列）。

---

## T6: 内置命令 + 假 context 测试

**文件：** `codepilot/commands/builtins.py`、`tests/test_commands_builtins.py`、`tests/test_commands_context.py`
**依赖：** T1（ctx）、T2、T4
**步骤：**
1. 定义 `REVIEW_PROMPT` 常量（预设提示词，如「请审查当前工作区最近 git 改动：正确性/安全/可维护性/边界覆盖，无改动则说明所见」）。
2. `build_registry() -> CommandRegistry`：逐条 `register` 18 条命令，各 handler 用 `ctx` 语义操作实现：
   - `/help`(LOCAL, alias `/?`)：遍历 `registry.all(include_hidden=False)` 按 kind 分组列出 name/usage/desc，`ctx.show`。
   - `/exit`(LOCAL, alias `/quit`)：`ctx.exit_app()`。
   - `/plan`(UI)：`ctx.set_axis(PLAN, announce=True)`；`return params or None`。
   - `/do`(UI)：`ctx.set_axis(BUILD, announce=True)`；`return params or None`。
   - `/mode`(UI)：params 非 manual/auto/strict → 报错；否则 `ctx.set_release(policy)`。
   - `/auto`/`/manual`/`/strict`(UI)：三条单命令，分别 `ctx.set_release(ReleasePolicy.AUTO/MANUAL/STRICT)`。
   - `/edits`(UI)：params on/off → 指定，否则 None(翻转)；`ctx.set_accept_edits(on)`。
   - `/compact`(UI)：`ctx.compact()`；返回 None。
   - `/continue`(UI)：`ctx.resume_loop()`；返回 None。
   - `/memory`(LOCAL)：`m = ctx.memory_report()`；有则 `ctx.show(m)` 否则提示未启用。
   - `/forget`(UI)：无 keyword → 提示用法；否则 `n = ctx.delete_memory(keyword)` → `ctx.show(已删除 n 条)`。
   - `/clear`(UI, alias `/cls`)：`ctx.clear_display()`。
   - `/session`(LOCAL)：`snap = ctx.snapshot()`；用 `codepilot.session.store.summarize(snap.session_file)` 组信息 → `ctx.show`。
   - `/permission`(UI)：`snap = ctx.snapshot()` → `ctx.show(标记/放行/自动编辑)`。
   - `/status`(LOCAL)：`snap = ctx.snapshot()` → `ctx.show(全量状态)`。
   - `/review`(PROMPT)：`return REVIEW_PROMPT`（转发 AI）。
   （`/help` 等需 registry 的 handler 用闭包捕获 build_registry 内的 registry。）
3. `tests/test_commands_context.py`：写一个假 `CommandContext`（记录 show 调用、set_axis 等），以 `FakeCtx` 驱动 `build_registry()` 的每个命令，断言行为。

**验证：** `pytest tests/test_commands_builtins.py tests/test_commands_context.py -v` 绿；`python -c "from codepilot.commands.builtins import build_registry, REVIEW_PROMPT"` 成功；`build_registry().assert_no_conflicts()` 通过（18 条不撞）。

---

## T7: 子包导出

**文件：** `codepilot/commands/__init__.py`
**依赖：** T2–T6
**步骤：**
1. 导出 `CommandSpec`、`CommandKind`、`CommandRegistry`、`parse_command`、`dispatch_command`、`CommandContext`、`StatusSnapshot`、`build_registry`、`make_completer`、`REVIEW_PROMPT`、`CommandConflictError`。

**验证：** `python -c "from codepilot.commands import build_registry, parse_command, dispatch_command, CommandRegistry"` 成功。

---

## T8: `ui/app.py` 分流器 + 接线

**文件：** `codepilot/ui/app.py`
**依赖：** T7
**步骤：**
1. `from codepilot.commands import parse_command` 改从包导入；构造 `__init__` 增入参：`command_registry`（或在此 `build_registry()`）、`completer`（make_completer(registry)）、保持已有注入。
2. 新增 `_CommandContextImpl`（组合 App）实现 `CommandContext`：`show`→写 RichLog；`submit_to_ai`→`_start_ai`；`set_axis`/`set_release`/`set_accept_edits`→复用现有 `_set_axis`/`_cmd_release`/`_cmd_edits` 核心；`compact`/`resume_loop`→`run_worker(_cmd_compact)` / 现有 continue 编排；`delete_memory`/`memory_report`→复用 `_cmd_forget`/`_cmd_memory` 内部；`clear_display`→`RichLog.clear()`（保留历史/上下文）；`snapshot`→组 `StatusSnapshot`；`exit_app`→`self.exit()`。
3. `_submit` 重构为分流器：
   ```
   pr = parse_command(text, registry)
   blank → return
   message → _start_ai(pr.text)
   unknown → ctx.show(f"未知命令 /{…}。输入 /help 查看可用命令")
   command → out = dispatch_command(pr.spec, ctx, pr.params)
             out 是 str → _start_ai(out)      # 转发(plan/do 带消息 / review)
             out 是协程 → run_worker(_finish(out))  # _finish await→若得 str 再 _start_ai
   ```
4. 抽 `_start_ai(text)`：现 `_submit` 的 AI 启动段（清 prompt/写 You/置 streaming/set_interval/run_worker(_process_chat)）。
5. `_refresh_status` 中模式标记：`axis.value=="build" → "DEFAULT"`、`"plan" → "PLAN"`，传 StatusBar。
6. 删除原 `_submit` 的硬编码 if-链命令分支（已移入 builtins/ctx）。`F2 cycle_release` 保留。

**注意：** 此步后 App 依赖 `commands/` 包。运行验证只跑 targeted + 手动 `python -c "import codepilot.ui.app"`。

**验证：** `python -c "import codepilot.ui.app"` 成功（无旧 `commands.py` 引用错误）；`pytest tests/test_commands_*.py -v` 仍绿。

---

## T9: Tab 补全 UI

**文件：** `codepilot/ui/widgets/prompt_input.py`、`codepilot/ui/screens.py`
**依赖：** T5（completer）、T8（接线传入）
**步骤：**
1. `PromptInput.__init__` 增可选 `completer: Callable[[str], list[str]] | None = None`。
2. `_on_key` 增 `tab` 分支：取光标所在行文本，若以 `/` 开头：
   - `cs = completer(line)`；`len(cs)==1` → 把行首 `/`+tokens 扩写为 `/<唯一名> `（`self._replace_via_keyboard` 替换该段）；
   - `len(cs)>1` → `self.post_message(CompletionRequested(cs, line))`；
   - `0` → 忽略。
3. `screens.py` 追加 `CompletionScreen(candidates, on_pick)`：`ListView` 列候选；Enter 选中 → `on_pick(名)` 把 `/<名> ` 填回 PromptInput 并 close；Esc close。定义 `PromptInput.CompletionRequested(Message)`。
4. `app.py` 接 `on_prompt_input_completion_requested` → `push_screen(CompletionScreen(cands, 填回回调))`；构造时给 PromptInput 传 `completer`。

**验证：** 手动（阶段六真机）：`/` + Tab 弹菜单、选后填入；`/p`+Tab 单选 `/permission `（或 `/plan `）。单测：`test_commands_completer.py` 已覆盖候选逻辑；UI 补全盘交互留真机。

---

## T10: 测试适配 + 全量回归

**文件：** `tests/test_commands.py`、`tests/test_commands_parser.py` 等（已建）
**依赖：** T2–T9
**步骤：**
1. 改写 `tests/test_commands.py`：把旧 `parse_command` 断言迁移到新 `ParseResult` 语义（用 `build_registry()` 建真实 registry 驱动），删除已由 `test_commands_parser.py` 覆盖的重复项。
2. 确认无遗留 `from codepilot.commands import parse_command` 旧签名调用（grep）。
3. 全量回归。

**验证：** `pytest -q` 全量绿（既有 308 + state14 新增）；`ruff check .` 无回归（如配置）。