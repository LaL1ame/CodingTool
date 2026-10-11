# SHARED_PROGRESS.md — 跨 Agent 共享工作进度

> 本文件用于向其他 Agent / 新会话同步 CodePilot 项目的**当前工作状态**。
> 触发词：用户说「**更新共享工作进度**」时，把当前工作内容压缩后写入本文件。
> 本文件保持精简——是「进度快照」，不是完整文档；细节各归各的 state 文档。

---

## 最近一次更新：2026-10-11

### 当前进行中工作
**state9：上下文管理 + Token 优化**（`docs/state9-context-management/`）
- **处于：四份文档全部批准 → 进入阶段五（开发）**。
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