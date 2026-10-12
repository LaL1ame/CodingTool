# SHARED_PROGRESS.md — 跨 Agent 共享工作进度

> 本文件用于向其他 Agent / 新会话同步 CodePilot 项目的**当前工作状态**。
> 触发词：用户说「**更新共享工作进度**」时，把当前工作内容压缩后写入本文件。
> 本文件保持精简——是「进度快照」，不是完整文档；细节各归各的 state 文档。

---

## 最近一次更新：2026-10-12

### 当前进行中工作
**state10：Agent 停止判定增强**（`docs/state10-agent-stop-policy/`）
- **处于：开发完成（263 测试绿）→ 待人工验收（阶段六）**。
- 目标：Agent 停止判定从「数轮数 + 全当 error」改为「进展感知 + 区别分类 + 可续接」。
- 方案（参考 Claude Code 机制对齐）：
  - F1: max_rounds 默认 10→18（state9 保证不爆窗口）。
  - F2: 达上限 = `paused(reason=limit)` 可续接检查点（非 error），同一持久 `_msgs` 续跑。
  - F3: 空转「软守卫」`_StallDetector`——lex 级(名+参数hash)重复 + 无文本进展 → stall_hint（提示换策略，仍跑）→ 持续才 paused(stall)。
  - F4: 停止 reason 结构化 done/limit/stall/unknown_tool/cancelled/error。
  - F5: /continue 命令续接。
- 8 任务全实现：T1 Delta 字段→T2 AgentConfig→T3 StallDetector→T4 停止判定重构→T5 /continue→T6 续接→T7 UI→T8 测试。
- **测试设计要点**：limit / unknown_tool / plan-injection 三测试各自 `stall_enabled=False` 隔离机制（三个停止阀正交）；stall 语义专测放 test_agent_resume.py::TestStallDetection。
- 提交：7 个独立 Conventional Commit（docs→T1→…→T8）。
- 待做：人工验收（阶段六），验 /continue 续接、空转软提示、达上限 paused。

### 前一 state（已完成开发）
**state9：上下文管理 + Token 优化**（`docs/state9-context-management/`）
- 开发完成（11 任务全实现，256 测试绿），等待人工验收（阶段六）。
- 两层压缩：层1 工具结果存盘(offload) + 层2 LLM 摘要(compactor)；TUI 上下文指示器。
- 前置 F0：工具结果跨消息轮持久（进程内；resume 恢复留后续）。
- 验收提示词清单已交付（用 thousands.py 分片读 /compact /熔断等）。
- 目标：给 CodePilot 装上两层压缩（层1 工具结果存盘 + 层2 LLM 结构化摘要），在有限 token 预算内长时间干活不溢出；TUI 实时显示上下文使用量。
- 关键裁决（已定）：
  - 前置改造 F0——工具结果**跨轮持久**（此前只在单次 agent.run ctx 内存活）。
  - 窗口上限 = config 显式配置（`context:` 段，如 `window: 200000`）。
  - 层1 阈值 = **token 量纲**。
  - 新增 F7：TUI 状态栏实时显示上下文使用量。
  - token 估算：不引精确 tokenizer，锚定上次 `usage.input_tokens` + 对增量按字符估算（code `chars/4`）。
- 新子包 `codepilot/context/`：estimator / budget / offload / compactor / manager 五模块。
- **task.md 执行顺序**：T1(工具结果跨轮持久，最高优先) → T2估算 → T3配置 → T4状态机 → T5层1存盘 → T6层2摘要 → T7 provider独立完成方法(可在T2后并行) → T8编排 → T9接入agent → T10 UI → T11 main装配。
- 新增共享文件：`SHARED_PROGRESS.md`（本文件，跨 agent 工作进度同步）。

### 已完成里程碑（历史）
- works 若干 state：权限系统、模式状态机、会话 JSONL resume、MCP 客户端（211 测试绿）。
- MCP 现状：功能完成，唯一遗留——真实 npx/node server 的 Windows spawn 兼容未打通（非协议缺陷，独立后续）。

---

## 存档规范（如何用本文件）
- 每个功能 state 有自己的完整文档（`docs/state<N>-<topic>/spec+plan+task+checklist.md`）。
- 本文件只放「下一步做什么、当前踩到哪些决策、接缝在哪」——供新 agent 快速接手上手。
- 用户说「更新共享工作进度」时，调用方把对话里的工作内容压缩进这里。