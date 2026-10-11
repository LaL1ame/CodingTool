# CodePilot 上下文管理 + Token 优化 Checklist

> 基于已批准 spec.md + plan.md + task.md。每项可运行/观察验证，聚焦系统行为。

## 实现完整性
- [ ] **estimator** 纯模块可导入，`estimate_text`/`TokenEstimator` 可用（验证：`python -c "from codepilot.context import TokenEstimator"`）。
- [ ] **budget** 状态机就绪，`ContextBudget` 可构造，`ContextConfig` 默认值正确（验证：单测）。
- [ ] **offload** 层1存盘可用，`ToolResultOffloader` 可构造（验证：`from codepilot.context.offload import ToolResultOffloader`）。
- [ ] **compactor** 摘要可用，`Compactor` 可构造（验证：`from codepilot.context.compactor import Compactor`）。
- [ ] **manager** 编排入口 `ContextManager` 暴露于 `codepilot.context`（验证：`from codepilot.context import ContextManager`）。

## 功能/验收（AC）
- [ ] **AC-F0**：跨两条 `send_message`，上一条工具结果仍留在上下文（验证：`test_manager.py` 集成测试，第二条 `build_context()` 含首条工具结果）。
- [ ] **AC-F1**：单个工具结果 > N token → 历史仅预览+路径、完整内容在磁盘文件、按路径可重读（验证：`test_offload.py` 断言历史精简 + `reopen_text` 读回）。
- [ ] **AC-F2**：同消息多工具结果合计 > M → 从大到小存盘直至压回阈值（验证：`test_offload.py::offload_oversized`）。
- [ ] **AC-F3**：历史逼近窗口（自动余量触限）→ 触发摘要；早期消息被结构化摘要替代，近期 ≥5 条或 ≥1 万 token 原文保留（验证：`test_budget.py` + `test_manager.py`）。
- [ ] **AC-F4**：摘要调用带「禁工具」约束；草稿→正式两条独立流程、草稿不入任何历史（验证：`test_compactor.py` 假 provider 断言请求无 tools；查历史无草稿）。
- [ ] **AC-F5**：压缩后对话含边界消息，提示「细节请重读，勿脑补」（验证：`test_compactor.py::build_boundary_message` 内容断言）。
- [ ] **AC-F6**：手动可触发压缩；摘要连续失败 3 次进入熔断态、停止自动重试；`reset_circuit` 可解除（验证：`test_budget.py`）。
- [ ] **AC-F7**：TUI 状态栏显示上下文占用/窗口，轻量/重量压缩后数字实时下降（验证：端到端 T10）。
- [ ] **AC-N1**：估算模块无 LLM 依赖、只对增量估算、锚定上次 usage（验证：`test_estimator.py` 纯函数断言）。
- [ ] **AC-N2**：任何压缩发生后用户原始消息仍在、未被改写（验证：`test_manager.py` 断言历史含用户原文）。
- [ ] **AC-N3**：`context:` 配置可设 window / 余量 / N / M 且生效（验证：`test_budget.py` / `test_config.py`）。
- [ ] **AC-N5**：摘要失败不崩应用、连续 3 次熔断（验证：`test_budget.py`）。

## 集成
- [ ] 层1 在 `agent._append_tool_results` 前拦截，大结果以替身（预览+路径）回灌 ctx（验证：`test_manager.py` / `test_agent.py`）。
- [ ] 摘要走 provider **独立非流式纯文本完成**方法，不入主对话 `_tokens_in/out`（验证：`test_compactor.py` 假 provider + `test_protocols.py`。
- [ ] TUI 状态栏区分「累计花费」与「当前占用」两槽位，量纲不混淆（验证：阅读 render 输出 + 端到端观察）。
- [ ] F0 持久化与 session resume 一致（验证：`load_session` 能读出含工具结果历史）。
- [ ] 所有公开接口有真实调用方（验证：全量 `pytest -q` 通过）。

## 验证与测试
- [ ] 项目无语法错误、`codepilot.context` 导入成功（验证：`python -c "import codepilot.context"`）。
- [ ] 所有单元测试通过、无回归（验证：`pytest -q` → 原有用例 + 新增 context 用例全绿）。
- [ ] lint 检查通过（验证：`ruff check .`，如有配置；否则 `python -m compileall codepilot`）。

## 端到端场景
- [ ] **场景1（F0+层1）**：在很大文件上 `read`（>N token）→ 对话历史只显示预览+路径，模型下轮仍能按路径继续读完整内容（验证：TUI 真机，stdout 只出现路径）。
- [ ] **场景2（手动压缩+熔断）**：`/compact` 手动触发 → 状态栏 context 占用下降、出现边界消息；连续 3 次失败 → 不再自动压缩、`/compact` 提示熔断态（验证：TUI + /compact 命令）。
- [ ] **场景3（自动触发）**：对话累积逼近窗口 → 自动生成摘要、早期消息被替换、近期原文保留、状态栏数字回落（验证：TUI 长对话，需配置小 window 模拟）。
- [ ] **场景4（TUI 指示器）**：任意对话中状态栏稳定显示 `ctx N/窗`，随消息增长；压缩后刷新下降（验证：TUI 观察）。

## 已人工验证/备注（开发后填写）
- [ ] 待验收