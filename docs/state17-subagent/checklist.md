# state17 SubAgent 系统 Checklist

> 每一项通过运行代码或观察行为验证，聚焦系统行为。

## 实现完整性

- [x] 角色数据模型完整：AgentRole（frozen，含 tools/blocked/model/max_rounds/permission/can_delegate）、SubAgentTask 字段齐备（验证：`tests/subagents/test_model.py` 断言）。
- [x] 四级角色加载生效：插件<内置<用户<项目 同名覆盖；单文件坏/缺失跳过不阻断；非法字段启动期 RoleError（验证：`tests/subagents/test_loader.py` 四级用例 + 校验用例）。
- [x] `agent` 工具常驻：system_exempt=True、side_effect=False，任何 Skill 白名单/plan 模式不消失（验证：`tests/subagents/test_tool.py` + `test_filters.py` 断言）。
- [x] `tool_filter` 注入点：`_build_tools` 末尾调用，不传时行为与现状一致（验证：`tests/test_agent_tool_filter.py`）。
- [x] 角色名册可发现（真机验收修正）：system prompt volatile 块 + agent 工具 description/role enum 均列出角色（验证：`tests/subagents/test_manager.py`、`test_tool.py`、`tests/test_subagent_injection.py`）。
- [x] 多层防线纯函数四场景：白名单∪系统 / 黑名单剔除 / agent 禁（can_delegate∧depth<max_depth 才可见）/ 后台与 plan 只读（验证：`tests/subagents/test_filters.py` 逐场景）。
- [x] 定义式/Fork 独立 Agent 构造：共享 provider/registry/hooks，独立 permission/context_manager/msgs（验证：`tests/subagents/test_runner.py` mock provider）。

## 集成

- [x] 委派中心完整：前台 await 摘要回灌 / 后台 create_task 登记 / 并发上限拒绝 / 深度递增（验证：`tests/subagents/test_manager.py`）。
- [x] 三种进后台逐项：显式 background、前台 timeout 转后台、`/bg` 手动切；Fork 强制后台（验证：`test_manager.py` + `test_tool.py` 断言）。
- [x] 取消与状态追踪：cancel → status=cancelled；tasks() 快照含状态/结果/用量（验证：`test_manager.py`）。
- [x] 命令接线：`/agents` 列出任务、`/bg <id>` 切后台，manager 未启用时安全降级（验证：`tests/test_commands_builtins.py`）。
- [x] UI 回灌接线：后台完成写系统行 + 摘要追加主对话 `_msgs`（不触发新一轮）；流式中先入队、收尾回灌（验证：`tests/test_subagent_repl.py`）。
- [x] main 装配：load_all(check_tools) + 注册 AgentTool + app 传 subagent_manager（验证：`python -c "import codepilot.main"`）。

## 验证与测试

- [x] 项目无语法错误、模块可导入（验证：`python -c "import codepilot.subagents, codepilot.ui.app, codepilot.main"`）。
- [x] 全量单测通过（验证：`python -m pytest -q` —— **548 passed**，既有 482 + state17 新增 66）。
- [x] lint 无回归（验证：`ruff check codepilot/subagents tests/subagents` 零告警；`ruff check .` 27 处既有基线未新增）。

## 端到端场景（阶段六真机 / TUI）

- [ ] 场景 1（定义式委派）—— **预期**：项目 `.agents/architect.md` 存在时，主对话让模型「用 architect 角色审查这段设计」，模型调用 `agent` 工具（mode=defined, role=architect），子 Agent 以角色身份独立跑到底，前台调用完成后摘要作为工具结果回灌，主 Agent 后续轮可引用结论。
- [ ] 场景 2（Fork 式继承）—— **预期**：主对话跑了一段较长上下文后，让模型「fork 当前上下文，起草一份总结」，模型调用 `agent` 工具（mode=fork），任务**立即显示已转后台**（不阻塞主循环），完成后系统行 + 摘要进主对话；provider 支持 cache 时日志可见 cache_read>0。
- [ ] 场景 3（多层防线）—— **预期**：`can_delegate: false`（默认）的角色子 Agent **看不到 `agent` 工具**（无法再委派）；`architect`（can_delegate: true）在多级时受 max_depth 上限约束（配 1 时子 Agent 内仍不可见 agent）；后台任务只读（side_effect 工具被滤）。
- [ ] 场景 4（后台管理）—— **预期**：`/agents` 列出全部后台任务（id/角色/状态/用量）；前台跑 `agent` 任务时按 **Ctrl+B**（输入框禁用，`/bg` 不可输入）切后台后主对话不阻塞、子 Agent 继续跑完；空闲时 `/bg <id>` 仍可用；并发超过 4 时新委派回灌「并发已满」。
- [ ] 场景 5（故障隔离）—— **预期**：让子 Agent 执行注定失败的任务（如未知角色），主 Agent 继续跑不崩溃，日志与系统行有明确 `error` 记录；取消后台任务后 status 变 cancelled。

## 验收口径

- [x] spec AC1–AC10 均有对应可观测条目（AC1↔四级加载 · AC2↔工具常驻 · AC3↔定义式 · AC4↔Fork · AC5↔隔离共享 · AC6↔回灌 · AC7↔多层防线 · AC8↔后台管理 · AC9↔故障隔离 · AC10↔零回归）。
- [ ] 人工真机清单（阶段五交付后对照）逐项登记实际结果，与预期不符如实记录为差异。（**待阶段六真机验收**）
