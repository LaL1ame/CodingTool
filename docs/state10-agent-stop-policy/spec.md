# CodePilot Agent 停止判定增强 Spec

## 背景

- `agent.run()` 当前以 `max_rounds=10`（`agent.py:161`）为硬终止：跑满 10 轮且未完成时 `yield Delta(error="达到迭代上限")`，被 UI 当错误展示。
- 两种真实情况被这个硬常数一刀砍死：
  1. **任务确实大**（多文件/多阶段检索+编辑，真的需要 >10 步）→ 被误杀。
  2. **模型空转**（反复 read 同一文件、同一死胡同打转、无信息增益）→ 该停却停得太晚，还和情况 1 混为同一个 error。
- 现状只有一个「负向安全网」：`consecutive_unknown`（连续未知工具）。没有「进展判定」——不知道「在前进但需更多步」还是「原地打转」。
- **state9 已释放余量**：`_msgs` 持久化 + 上下文压缩 → 提高轮数上限不会撑爆窗口，`max_rounds` 不再「危险地放长绳子」。
- **参考 Claude Code**：上限是千级「安全兜底」非「正常终止路径」；达到上限是**可续接检查点**（`/continue` 带历史续跑），非死；空转靠「重复调用检测 + 模型自觉 + 系统提示」，硬循环天花板只作兜底。

## 目标

- 让 Agent 停止判定从「数轮数 + 全当 error」改为「**进展感知 + 区别分类 + 可续接**」：
  1. 大任务不再被 10 轮误杀（上限调高 + config 可调 + state9 兜底窗口）。
  2. 空转被提前识别并被引导转向（而非默默硬停）。
  3. 停止原因结构化、TUI 可辨识（完成任务 / 超限可续 / 空转提示 / 未知工具 / 用户取消）。
  4. 达到上限是**可续接检查点**而非死亡——同一持久历史可继续。

## 功能需求

### F1（上限可调 + 合理默认）
- F1.1 `max_rounds` 默认改为 **18**（可 config.agent.max_rounds 覆盖，机制已有）。
- F1.2 提高上限的安全性由 state9 保证（上下文可压缩、`_msgs` 持久不爆窗口）；本需求不涉及窗口管理。

### F2（超限 = 可续接检查点，非 error）
- F2.1 Agent 达到 `max_rounds` 且未完成时，`yield Delta(paused=True)`（**非** error），附 `reason="limit"`、已执行轮数与上限。
- F2.2 调用方（ChatManager/UI）收到 paused 后可**沿用同一持久 `_msgs` 再次触发 `run()`** 续跑，不丢失已完成的工作与历史。
- F2.3 续接可多次发生（每轮达到上限暂停 → 用户/策略决定是否续）。

### F3（空转提示：软守卫）
- F3.1 Agent 检测到**重复调用**：最近 K 轮内同一 `(工具名, inspect.signature(arguments))` 出现 ≥2 次且无**文本进展** → 判定为潜在空转。
- F3.2 判定空转 **不是硬停**，而是 `yield Delta(stall_hint=True)`：附带提示文本，引导模型**改变策略**（换工具/换参数/重新明确目标）。
- F3.3 若下一轮仍是重复（持续空转）→ 才真正 `yield Delta(paused=True, reason="stall")` 停，避免无限打转。
- F3.4 空转判定是 **lex 级确定性检测**（基于调用签名 + 参数 hash），不依赖语义判断——可靠、不误伤「慢但前进」的任务。

### F4（停止原因结构化）
- F4.1 所有非 `done` 的停止都带结构化 `reason`，枚举：`done` / `limit` / `stall` / `unknown_tool` / `cancelled` / `error`。
- F4.2 对应 Delta 字段透传给 UI，使 TUI 状态栏/日志能区分「任务完成 / 超限可续 / 空转 / 未知工具 / 取消 / 真实错误」。
- F4.3 超限与空转的停止**不再当作 error**（不显示红色错误），而是友好的「可续接/建议换策略」提示。

### F5（UI 续接交互）
- F5.1 TUI 收到 `paused(limit)` 后，显示「已达到迭代上限，可输入 continue 续跑」。
- F5.2 新增 `/continue` 命令：沿用当前 `_msgs` 再触发一次 `run()`（续接）。
- F5.3 收到 `stall_hint` 时，日志/状态栏提示「检测到可能空转，模型已建议换策略」。

## 非功能需求

### N1（零错误误报）
- N1.1 `paused(limit)` / `paused(stall)` / `stall_hint` **不得**被当成 error 呈现给用户（UI 不显示红色错误）。
- N1.2 空转判定（F3.1）优先避免误伤：仅当「重复调用**且**无文本进展」才触发，且首达是 `stall_hint`（提示）而非 `paused`（停）。

### N2（可续接 / 无副作用重启）
- N2.1 续跑用同一持久 `_msgs`，不重放已完成工具副作用（`_execute_batched` 只执行当前批，不重跑历史）。
- N2.2 暂停点与续接点之间状态一致：`messages` 完整保留（已回灌的助手/工具结果都在）。

### N3（低开销）
- N3.1 空转检测为纯内存、O(工具调用量)、无 I/O、无 LLM——每轮成本远小于一次模型调用。
- N3.2 签名/参数 hash 用 Python 原生（`repr` + `hash` 或 hashlib），不引入依赖。

### N4（可配置）
- N4.1 空转阈值参数（重复次数 K、窗口轮数、是否启用空转检测）经 config `agent:` 段可调，有合理默认。
- N4.2 `max_rounds` 保持可配（既有 AgentConfig）。

### N5（向后兼容）
- N5.1 增量 `paused`/`stall_hint` Delta 字段不破坏既有消费方（`Delta.paused`、`Delta.stall_hint` 默认 None/False，旧逻辑读不到即忽略）。
- N5.2 不引入新依赖。

## 不做的事

- **不做自动无限续跑**：paused 后由用户 `/continue` 或显式策略续，不自动悄悄无限续——避免隐藏失控循环（F2 决定为手动续）。
- **不做语义级「进展理解」**：空转判定用 lex 级签名+参数检测，不做「模型是否真在接近目标」的语义推理（成本高、不可靠、误伤大）。
- **不改 `max_rounds` 到干级**：18 是合理平衡（不学 Claude Code 的数千轮——那依赖更强的自纠错和更可靠的窗口管理，超出本轮）。
- **不做子任务（Task tool）模式**：重活用「子任务编排」是更大课题，记入 roadmap，本轮只在单循环内做停止判定优化。
- **不动 context 压缩 / MCP / 权限**：本轮纯 agent 停止判定 + Delta 协议 + UI 提示，不跨界。

## 验收标准

- **AC-F1**：默认 `max_rounds=18`；config 覆盖后生效（单测读 AgentConfig 默认 + 覆盖）。
- **AC-F2**：达上限未完成时 `yield Delta(paused=True, reason="limit")` 而非 error；带着同一 `_msgs` 再触发 run 能续跑，已完成轮次与历史不丢（单测）。
- **AC-F3**：重复调用且无文本进展 → `stall_hint`；持续到下一轮仍重复 → `paused(reason="stall")`；「慢但前进」（有文本/有不同参数）不触发（单测）。
- **AC-F4**：停止 reason 结构化，`done/limit/stall/unknown_tool/cancelled/error` 各可区分（单测断言 Delta.reason/Delta.paused）。
- **AC-F5**：TUI 收到 paused 显示「可输入 /continue 续跑」；`/continue` 沿用同一 `_msgs` 续跑；stall_hint 显示提示（端到端/人工）。
- **AC-N1**：paused/stall_hint 在 UI 不显示为 error（端到端观察 / UI 单测）。
- **AC-N2**：续接不重放副作用（集成测试：续接后副作用工具执行次数不重复计数）。
- **AC-N3**：空转检测无 I/O、纯内存（实现 + 观察）。
- **AC-N4**：空转阈值经 config 可调（单测注入 config）。
- **AC-N5**：新 Delta 字段默认 None/False，旧消费方不受影响（回归全绿）。

## 验收结论（开发后填写）

- [ ] 待验收