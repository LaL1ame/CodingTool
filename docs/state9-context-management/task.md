# CodePilot 上下文管理 + Token 优化 Tasks

> 基于已批准 spec.md + plan.md。F0（工具结果跨轮持久）排 T1，是层1/层2 的前提。

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `codepilot/context/__init__.py` | 导出 ContextManager, ContextConfig |
| 新建 | `codepilot/context/estimator.py` | TokenEstimator + estimate_text（纯估算） |
| 新建 | `codepilot/context/budget.py` | ContextBudget + ContextConfig（配置模型） |
| 新建 | `codepilot/context/offload.py` | ToolResultOffloader（层1存盘） |
| 新建 | `codepilot/context/compactor.py` | Compactor（层2摘要+边界消息） |
| 新建 | `codepilot/context/manager.py` | ContextManager（编排） |
| 新建 | `tests/context/test_estimator.py` | 估算单测 |
| 新建 | `tests/context/test_budget.py` | 状态机/触发/熔断单测 |
| 新建 | `tests/context/test_offload.py` | 层1存盘/重读/降级单测 |
| 新建 | `tests/context/test_compactor.py` | 摘要 prompt/边界消息/失败单测 |
| 新建 | `tests/context/test_manager.py` | 编排 + F0 跨轮持久回归 |
| 修改 | `codepilot/config.py` | ContextConfig 读取（`context:` YAML 段） |
| 修改 | `codepilot/chat.py` | F0：build_context 纳入工具结果；接收 manager |
| 修改 | `codepilot/agent.py` | 层1：prep_tool_result 拦截 |
| 修改 | `codepilot/providers.py`/`protocols.py` | 新增非流式纯文本完成方法 |
| 修改 | `codepilot/ui/app.py`、`ui/widgets/status_bar.py` | F7 显示 |
| 修改 | `codepilot/main.py` | 装配 ContextManager 并注入 |

## T1: 工具结果跨轮持久（F0）【架构前置，最高优先】

**文件：** `codepilot/chat.py`
**依赖：** 无
**步骤：**
1. 在 `ChatManager` 增加工具结果存储：新增 `tool_history: list`（或把工具结果并入 history 的消息类型，用 `role="tool"` 区分），记录每个工具结果 `{call_id, name, output, error}`。
2. `build_context()` 序列化时把 tool_history 中的工具结果一并产出（按协议改为 dict 形式，对齐 agents.anthropic `tool_result` / openai `tool` 形态）。
3. 需与 `agent.run(ctx)` 回灌的内容协同：`send_message` 结束时把本轮产生的工具结果归档进 `tool_history`（否则丢）。
4. `session/store.py` 可恢复性：`load_session` 时若历史含工具结果则恢复，resume 一致（F0.2）。

**验证：** `tests/context/test_manager.py::TestF0Persistence`：跨两条 `send_message`，第二条的 `build_context()` 仍含第一条的工具结果；`load_session` 能读出。

## T2: Token 估算模块（N1）

**文件：** `codepilot/context/estimator.py`、`tests/context/test_estimator.py`
**依赖：** 无
**步骤：**
1. `estimate_text(text, kind)`：`code` → `len(tokens)=chars//4`；`prose` → `words×1.3`。
2. `TokenEstimator`：`anchor(input_tokens)` 重锚；`add(text, kind)` 返回增量；`current()` 返回锚点+累计增量。
3. 纯函数、无 I/O、无 LLM 依赖。

**验证：** 单测断言 `estimate_text("a"*1000)` ≈ 250；`anchor(1000)` 后 `add("...")` 的 `current()` 为 `1000+Δ`。

## T3: 配置模型 + YAML 读取（N3）

**文件：** `codepilot/context/budget.py`（定义 ContextConfig）、`codepilot/config.py`（读）、`tests/context/test_budget.py`
**依赖：** 无
**步骤：**
1. 在 `budget.py` 定义 `ContextConfig` dataclass（window/auto_margin/manual_margin/n/m/keep）。
2. `config.py` 新增 `load_context_config(path)` 读 `context:` 段、缺省用默认，**ContextConfig 唯一真源在 budget.py**（config.py 只造对象不回定义）。
3. 测试 `tests/context/test_budget.py` 里覆盖「无 context 段→默认」「有段→覆盖」。

**验证：** `load_context_config` 返回的默认 window==200_000；配了 `window: 128000` 后返回 128000。

## T4: ContextBudget 状态机（F3/F6）

**文件：** `codepilot/context/budget.py`、`tests/context/test_budget.py`
**依赖：** T2, T3
**步骤：**
1. `occupied` 由 estimator 的 `current()` 提供。
2. `should_compact()`：`occupied > window - auto_margin`（且非熔断）→ True。
3. `register_summarize_failure`：连续计数，>=3 置 `is_openckety`；`summarize_succeeded` 清零。
4. `reset_circuit`：解除熔断。
5. `record_offload(saved)`：occupied 估算减 saved（层1回收后保持口径一致）。

**验证：** 单测：逼近窗口返回 True；连续 3 次失败后 `is_openckety`；reset 后恢复。

## T5: 层1 工具结果存盘（F1/F2）

**文件：** `codepilot/context/offload.py`、`tests/context/test_offload.py`
**依赖：** T2
**步骤：**
1. `offload(result)`：预估 token > N → 写 JSON/文本文件到 disk，返回 `OffloadedResult`（预览片 + 路径 + 替身内容）。
2. `offload_oversized(results)`：累计 > M，按 token 从大到小逐个 offload，直到压回阈值；返回处理后的结果列表。
3. `reopen_text(path, max_chars)`：按路径读完整内容，供模型重读。
4. 磁盘写失败 → 降级：返回仅预览、不丢原文（N5）。

**验证：** 单测：大结果被写盘、历史只剩预览+路径；`reopen_text` 能读回；磁盘只读时降级不抛。

## T6: 层2 LLM 摘要（F3/F4/F5）

**文件：** `codepilot/context/compactor.py`、`tests/context/test_compactor.py`
**依赖：** T3
**步骤：**
1. `build_summary_prompt(overview, recent)`：固定部分组织 + **禁工具指令**（F4.1）。
2. `compact(provider, model, ...)`：走 provider **新完成的非流式纯文本方法**，先草稿后正式（草稿内部变量，用完即弃，F4.2/4.3），返回结构化摘要。
3. `build_boundary_message()`：F5 边界消息（提示重读、勿脑补）。
4. 失败行为：抛异常（交由 budget 计熔断，不自行吞）。

**验证：** 单测（假 provider）：prompt 含禁工具；草稿不入任何历史；返回摘要；`build_boundary_message` 内容含「重读」。

## T7: 非流式纯文本完成方法（F4.3 载体）

**文件：** `codepilot/providers.py`、`codepilot/protocols.py`
**依赖：** 无
**步骤：**
1. 在 `BaseProvider` 增加 `async complete(system: str, messages: list[dict]) -> str` 抽象方法。
2. `AnthropicProvider`/`OpenAIProvider` 实现：非流式 POST，`tools` 不传（禁工具），返回纯文本。
3. 保证其 usage 不影响主对话 `_tokens_in/out` 累计（独立，不入 TUI 计费显示）。

**验证：** `tests/context/test_compactor.py` 用假 provider 实现 `complete`，断言调用参数无 tools。

## T8: ContextManager 编排（对外 API）

**文件：** `codepilot/context/manager.py`、`tests/context/test_manager.py`
**依赖：** T2–T7
**步骤：**
1. 组合 estimator/budget/offloader/compactor/provider/chat。
2. `pre_request()`：先层1批（本消息工具结果 offload_oversized），再 `should_compact()` → 层2（compactor.compact → 改 chat.history 替早期/留近期/追边界消息 → budget 计成功）。
3. `prep_tool_result(result)`：层1 单结果；返回替身，供 agent 回灌。
4. `compact_manual()`：手动（manual_margin）。
5. `current_usage_text(manual=...)`：F7 显示串。
6. `on_usage_anchor(input_tokens)`：每轮 usage 重锚 estimator。

**验证：** `test_manager.py`：预请求触发 patch 掉 compactor，断言边界消息入历史；失败 3 次熔断。

## T9: F0 + 层1 接入 agent（合并持久 + 拦截）

**文件：** `codepilot/agent.py`
**依赖：** T1, T8
**步骤：**
1. `Agent` 接收 manager（可选注入）。
2. `_append_tool_results` 前，对每个 result 调 `manager.prep_tool_result`，用返回替身替换。
3. 保证 `run` 的 messages 回灌仍含替身（预览+路径），完整内容在盘。

**验证：** `test_agent.py` 或 `test_manager.py`：注入 manager 后，大结果入 ctx 的是预览+路径替身。

## T10: TUI 上下文指示器（F7）

**文件：** `codepilot/ui/app.py`、`codepilot/ui/widgets/status_bar.py`
**依赖：** T8
**步骤：**
1. `StatusBar.update_state` 增加 `usage: str` 入参（如 `ctx 45K/200K`），复用渲染。
2. `app._refresh_status` 调 `manager.current_usage_text()`；`on_usage_anchor`/`record_offload`/压缩后调用 `_refresh_status` 实时刷新。
3. 区分「累计花费」与「当前占用」两个槽位，避免量纲混淆。

**验证：** 端到端 (checklist T10)：对话中状态栏出现 `ctx ...K/...K`，触发压缩后数字下降。

## T11: main.py 装配接线

**文件：** `codepilot/main.py`
**依赖：** T8–T10
**步骤：**
1. 读 `context` 配置 → 构建 context 子模块 → 建 `ContextManager`。
2. 注入 Agent 与 ChatManager；App 构造传 manager（或回调）。
3. `find_config` 已有，复用取 context 段。

**验证：** 应用启动不报错（人工 + 导入冒烟）。

## 执行顺序

```
T1(前置持久化)
  ↓
T2 → T3
  ↓     ↓
T4 ←── T3
  ↓
T5 ←── T2      T7(独立完成方法)
  ↓            ↓
T6 ←── T3 ←  T7   （compactor 依赖 T7）
  ↓
T8(编排，聚合 T2-T7)
  ↓
T9(GUI写死分支：T1+T8 接入 agent)
  ↓
T10(UI) ── T11(main 装配，最后串起来)
```

> T7 虽后列，但 T6 依赖它；实际上 T7 与 T2–T5 无依赖，可在 T2 后即并行起步。执行时以「T7 先于 T6」为准。