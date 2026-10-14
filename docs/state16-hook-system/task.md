# state16 Hook 系统 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 修改 | `codepilot/permissions/rules.py` | match_text 升级四语法（!反向 / /regex/ / glob / 精确） |
| 新建 | `codepilot/hooks/model.py` | HookRule/Condition/CondItem/HookAction/HookError/EVENTS/事件字段注册表 |
| 新建 | `codepilot/hooks/loader.py` | 两级 YAML 加载 + F10 集中校验 |
| 新建 | `codepilot/hooks/injector.py` | HookInjector：prompt 动作 volatile 块仓库 |
| 新建 | `codepilot/hooks/actions.py` | 四动作执行器（command/http/prompt/subagent） |
| 新建 | `codepilot/hooks/engine.py` | HookEngine：匹配/fire/fire_pre_tool/once/async/timeout/aclose |
| 新建 | `codepilot/hooks/__init__.py` | 导出 HookEngine/HookInjector/load_hooks/HookError |
| 修改 | `codepilot/prompt/builder.py` | build_system_prompt 加 hook_blocks 参数 |
| 修改 | `codepilot/agent.py` | 4 发射点 + hooks/hook_injector 可选参数 |
| 修改 | `codepilot/chat.py` | user_message 发射点 + hooks 可选参数 |
| 修改 | `codepilot/context/manager.py` | on_compact 可选回调 |
| 修改 | `codepilot/ui/app.py` | session_start/end 发射点 + notify 接线 + /clear 联动 |
| 修改 | `codepilot/main.py` | 装配 + HookError 启动报错 |
| 新建 | `tests/hooks/test_match.py` | 四语法 + 权限既有行为回归 |
| 新建 | `tests/hooks/test_loader.py` | 两级加载 + F10 校验全集 |
| 新建 | `tests/hooks/test_actions.py` | command 退出码/stderr/env、http、subagent 占位 |
| 新建 | `tests/hooks/test_engine.py` | 匹配/fire/once/async/timeout/声明顺序 |
| 新建 | `tests/hooks/test_injection.py` | prompt 注入 volatile 块/去重/clear 复位 |
| 新建 | `tests/hooks/test_intercept.py` | tool_pre 拦截/放行/fail-open/权限先于 Hook/round/stop 发射 |

## T1: 共享匹配器四语法升级

**文件：** `codepilot/permissions/rules.py`、`tests/hooks/test_match.py`
**依赖：** 无
**步骤：**
1. `match_text` 加 `!` 前缀反向（剥离前缀后递归走下层判定，结果取反）。
2. 加 `/.../`（首尾均 `/`、长度≥2）→ `re.search`；`re.error` 防御性返回 False。
3. glob（含 `* ? [`）与精确路径保持原样。
4. 新测试：四语法命中/不命中、`!` 包裹三种下层语法、非法正则 no-match、既有精确/glob 行为回归（直接复用现权限测试的用例形态）。

**验证：** `pytest tests/hooks/test_match.py tests/test_permissions_policy.py -q` 全绿。

## T2: hooks/model.py 数据模型

**文件：** `codepilot/hooks/model.py`
**依赖：** 无
**步骤：**
1. `HookError(Exception)`。
2. `EVENTS` 8 元组 + `ACTION_TYPES = ("command","prompt","http","subagent")`。
3. `EVENT_FIELDS: dict[str, set[str]]`——非 tool 事件的保留字段注册表（session_start:{resumed}、round_start:{round}、user_message:{text}、tool_pre/tool_post:{tool,ok}+参数放开、stop:{reason}、session_end/compact:∅）。
4. 四个 frozen dataclass（CondItem/Condition/HookAction/HookRule），字段同 plan。

**验证：** `python -c "from codepilot.hooks.model import HookRule, EVENTS"` 导入成功。

## T3: hooks/loader.py 加载与集中校验

**文件：** `codepilot/hooks/loader.py`、`tests/hooks/test_loader.py`
**依赖：** T1、T2
**步骤：**
1. `load_hooks(project_path, user_path) -> list[HookRule]`：用户级在前、项目级在后；文件缺失/空/`hooks:` 为空 → 跳过。
2. 逐条解析：event 必填、action 必填（dict，含 type）、if 可省略；简写 `if: {field: pattern}` → all 单条；`if.all`/`if.any` 列表。
3. F10 全集校验，违反抛 `HookError`（消息含来源文件+序号）：未知 event、未知 action.type、all/any 混用或同现、条件条目空、正则编译失败（用 T1 语法判定后 re.compile）、command 缺 cmd、http 缺 url、prompt 缺 text、tool_pre 配 async、非 tool 事件条件字段不在注册表。
4. timeout/async/once 从 action dict 提取为 HookAction 字段（timeout 默认 None → engine 用全局默认）。
5. 测试：两级合并顺序、缺文件忽略、F10 逐条触发。

**验证：** `pytest tests/hooks/test_loader.py -q` 全绿。

## T4: hooks/injector.py

**文件：** `codepilot/hooks/injector.py`
**依赖：** T2
**步骤：**
1. `HookInjector`：`add(key, text)`（dict 保插入序，同 key 覆盖=去重）、`hook_blocks()`（values 列表，空→[]）、`clear()`。

**验证：** `python -c "from codepilot.hooks.injector import HookInjector"` 导入成功（行为测试在 T8）。

## T5: hooks/actions.py 四动作执行器

**文件：** `codepilot/hooks/actions.py`、`tests/hooks/test_actions.py`
**依赖：** T4
**步骤：**
1. 占位符渲染 `_render(text, fields)`：`str.format_map` + 缺字段容错类（缺 key 保留 `{...}` 原样）。
2. `run_command(cmd, fields, timeout, event, tool) -> CommandOutcome(exit_code, stdout, stderr, timed_out)`：`asyncio.create_subprocess_shell`，env 叠加 `CODEPILOT_HOOK_EVENT/CODEPILOT_TOOL/CODEPILOT_TOOL_ARGS(JSON)`；`asyncio.wait_for` 超时杀进程。
3. `run_http(url, method, headers, body, timeout) -> (ok, detail)`：urllib + `asyncio.to_thread`；异常捕获返回 (False, str(e))。
4. `run_prompt(text, fields, injector, key)`：渲染后 `injector.add`。
5. `run_subagent()`：记日志「未实现」，返回 (True, "skipped")。
6. 测试：command 退出码 0/非 0/stderr 捕获/超时/env 传入（用 python -c 回显）；http 用本地 http.server 起停 fixture；prompt 落 injector；subagent 记日志。

**验证：** `pytest tests/hooks/test_actions.py -q` 全绿。

## T6: hooks/engine.py 核心引擎

**文件：** `codepilot/hooks/engine.py`、`tests/hooks/test_engine.py`
**依赖：** T3、T5
**步骤：**
1. `HookEngine(rules, injector, notify=None)`；规则按 event 预分组。
2. fields 入口统一 `_str_fields`（bool→"true"/"false"、int→str、其余 str()）。
3. 条件判定：None 恒真；all/any 按 match_text。
4. `fire(event, fields)`：零规则短路；命中按声明顺序；once 键 `(source, ordinal)` 已发跳过；is_async → create_task 入集合（done 回调：异常/失败记日志 + notify `[hook] <source>#<ordinal> failed/done`），否则 await 动作执行（异常吞掉记日志，N2）。
5. `fire_pre_tool(tool, arguments)`：命中 tool_pre 规则；command 动作同步逐个跑，首个 exit_code≠0 → 返回 stderr（截断 500 字符，空则给模板原因）；异常/超时记日志继续（fail-open）；非 command 动作照执行（不影响判定）。
6. `aclose()`：有界等待后台任务（默认 5s），未完成取消。
7. 默认超时：command 60s、http 30s（action.timeout 覆盖）。
8. 测试：匹配/无条件、声明顺序、once 二次跳过、async 不阻塞且完成有 notify、timeout、aclose 取消。

**验证：** `pytest tests/hooks/test_engine.py -q` 全绿。

## T7: hooks/__init__.py

**文件：** `codepilot/hooks/__init__.py`
**依赖：** T6
**步骤：**
1. 导出 `HookEngine`、`HookInjector`、`load_hooks`、`HookError`、`EVENTS`。

**验证：** `python -c "import codepilot.hooks; codepilot.hooks.HookEngine"` 成功。

## T8: prompt/builder.py hook_blocks 参数 + 注入测试

**文件：** `codepilot/prompt/builder.py`、`tests/hooks/test_injection.py`
**依赖：** T4
**步骤：**
1. `build_system_prompt(..., hook_blocks: Sequence[str] = ())`，volatile 中紧跟 skill_blocks 之后 extend。
2. 测试：hook 块位置钉住（env 后、plan/memory 前）；injector add 去重（同 key 覆盖）；clear 复位；空 injector → 与现状输出一致（兼容）。

**验证：** `pytest tests/hooks/test_injection.py tests/test_prompt*.py -q` 全绿。

## T9: agent.py 四发射点

**文件：** `codepilot/agent.py`、`tests/hooks/test_intercept.py`
**依赖：** T7、T8
**步骤：**
1. 构造加 `hooks=None`、`hook_injector=None`；`build_system_prompt` 调用传 hook_blocks。
2. `round_start`：每轮循环顶 fire（fields={round}）。
3. `tool_pre`：`_execute_one` 权限通过后 `reason = await hooks.fire_pre_tool(...)`；reason → `ToolResult(error=f"Hook 拦截: {reason}")`。
4. `tool_post`：拿到结果后 fire（fields 含 tool+参数+ok）。
5. `stop`：done/limit/stall/unknown_tool 四出口收拢（提取 `_emit_stop(reason)` 辅助），fire fields={reason}。
6. 全部 `self._hooks is None` 短路。
7. 测试：拦截后工具未执行且 error 回灌、放行正常执行、hook 异常 fail-open、权限拒绝不触发 tool_pre（mock engine 断言未调用）、round_start/stop 按轮/按出口触发。

**验证：** `pytest tests/hooks/test_intercept.py tests/test_agent*.py -q` 全绿。

## T10: chat.py user_message 发射点

**文件：** `codepilot/chat.py`
**依赖：** T9
**步骤：**
1. 构造加 `hooks=None`；`send_message` 非 resume 分支，`_msgs.append` 后、agent.run 前 `await hooks.fire("user_message", {"text": content})`。

**验证：** `pytest tests/test_chat.py -q` 全绿（发射断言并入 test_intercept 或本文件既有测试扩展）。

## T11: context/manager.py on_compact 回调

**文件：** `codepilot/context/manager.py`
**依赖：** 无（回调形态，不 import hooks）
**步骤：**
1. 实例属性 `on_compact: Callable[[], Awaitable[None]] | None = None`；`_do_compact` 成功返回前 `if self.on_compact: await self.on_compact()`。

**验证：** `pytest tests/context/ -q` 全绿。

## T12: ui/app.py 接线

**文件：** `codepilot/ui/app.py`
**依赖：** T9、T10
**步骤：**
1. 构造加 `hook_engine=None`、`hook_injector=None`（main 传入）。
2. `on_mount` 末尾 `run_worker(engine.fire("session_start", {"resumed": bool(self._seed_msgs)}))`（engine 非 None 才发）。
3. `on_unmount`：best-effort fire("session_end") + `engine.aclose()`。
4. `engine.notify = self._hook_notify`：写 RichLog 系统行 `[dim][hook] ...[/dim]`。
5. `_clear_display` 追加 `hook_injector.clear()`。
6. ChatManager 构造传 `hooks=engine`。

**验证：** `pytest tests/ -q` 全绿（headless 测试不回归）。

## T13: main.py 装配

**文件：** `codepilot/main.py`
**依赖：** T12
**步骤：**
1. `load_hooks(workspace/".hooks.yaml", Path.home()/".hooks.yaml")`；`HookError` → 打印 + `sys.exit(1)`（与 ConfigError 同款）。
2. injector → engine(rules, injector) → Agent(hooks=engine, hook_injector=injector.hook_blocks)。
3. app 构造传 hook_engine/hook_injector；`context_manager.on_compact = lambda: engine.fire("compact", {})`。

**验证：** `python -c "import codepilot.main"` + `pytest -q` 全量 391+ 绿 + `ruff check codepilot/hooks tests/hooks` 零告警。

## 执行顺序

```
T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8 → T9 → T10 → T11 → T12 → T13
        （T2 起纯顺序链；T11 可在 T6 后任意位置，无依赖）
```
