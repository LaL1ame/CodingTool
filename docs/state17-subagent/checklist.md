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
- [x] Fork 历史协议合法化（真机验收修正）：裁剪父历史尾部未回灌的 tool_calls，避免 tool_use 后缺 tool_result 的 400（验证：`test_runner.py` 两种协议裁剪用例）。

## 集成

- [x] 委派中心完整：前台 await 摘要回灌 / 后台 create_task 登记 / 并发上限拒绝 / 深度递增（验证：`tests/subagents/test_manager.py`）。
- [x] 三种进后台逐项：显式 background、前台 timeout 转后台、`/bg` 手动切；Fork 强制后台（验证：`test_manager.py` + `test_tool.py` 断言）。
- [x] 取消与状态追踪：cancel → status=cancelled；tasks() 快照含状态/结果/用量（验证：`test_manager.py`）。
- [x] 命令接线：`/agents` 列出任务、`/bg <id>` 切后台，manager 未启用时安全降级（验证：`tests/test_commands_builtins.py`）。
- [x] UI 回灌接线：后台完成写系统行 + 摘要追加主对话 `_msgs`（不触发新一轮）；流式中先入队、收尾回灌（验证：`tests/test_subagent_repl.py`）。
- [x] main 装配：load_all(check_tools) + 注册 AgentTool + app 传 subagent_manager（验证：`python -c "import codepilot.main"`）。
- [x] 轨迹扁平化（F11 增量）：Anthropic 块 / OpenAI tool_calls / role=tool 均转 `TranscriptEntry`，工具名映射、截断、start 切片、不改入参（验证：`tests/subagents/test_transcript.py`，7 用例）。
- [x] 轨迹存储：成功/失败/达上限均写入 `task.entries`；Fork 只存自有部分（验证：`test_runner.py` / `test_manager.py`）。
- [x] F3 任务面板：列表 ↑↓ 选择、Enter 打开轨迹、Esc 返回且索引保持；0.5s 刷新不打断选中；新任务追加不扰动选中（验证：`tests/test_subagent_screen.py`，10 用例）。
- [x] 后台完成摘要直显：聊天区出现「子任务 #N 结论」+ 摘要，同时仍进入 `_msgs`；markup 字符原样显示（验证：`tests/test_subagent_repl.py`）。
- [x] 提示行与焦点：F3 打开面板、Esc 关闭后输入框焦点恢复；流式中开面板、结束后仍可输入（验证：`tests/test_subagent_app.py`）。

## 验证与测试

- [x] 项目无语法错误、模块可导入（验证：`python -c "import codepilot.subagents, codepilot.ui.app, codepilot.main"`）。
- [x] 全量单测通过（验证：`python -m pytest -q` —— **584 passed**，state17 主体 548 + 观察视图/恢复修正 36）。
- [x] lint 无回归（验证：`ruff check codepilot/subagents tests/subagents` 零告警；`ruff check .` 27 处既有基线未新增）。

## 端到端场景（阶段六真机 / TUI）

- [x] 场景 1（定义式委派）—— **真机通过**：模型调用 `agent`（mode=defined, role=architect），子 Agent 以角色身份独立跑到底，摘要回灌后可引用。
- [x] 场景 2（Fork 式继承）—— **真机通过**：fork 强制后台不阻塞主循环，完成后摘要直显并进入主对话上下文。
- [x] 场景 3（多层防线）—— **真机通过**：子 Agent 默认不可见 `agent`；后台任务只读；深度/黑名单约束生效。
- [x] 场景 4（后台管理）—— **真机通过**：`/agents` 列表、**Ctrl+B** 前台转后台后主对话继续可输入、`/bg <id>` 空闲时可用。
- [x] 场景 5（故障隔离）—— **真机通过**：失败任务只影响自身，日志/系统行有明确错误记录，主 Agent 不崩溃。
- [x] 场景 6（子任务观察视图，增量 B）—— **真机通过**：前台定义式任务完成后可在 F3 查看轨迹；后台 fork 提示行、完成摘要直显；F3 列表 ↑↓/Enter/Esc 与选中保持；`[x] /agents <file>` 在轨迹中原样显示；F3 开关后可直接继续输入。

## 验收口径

- [x] spec AC1–AC10 均有对应可观测条目（AC1↔四级加载 · AC2↔工具常驻 · AC3↔定义式 · AC4↔Fork · AC5↔隔离共享 · AC6↔回灌 · AC7↔多层防线 · AC8↔后台管理 · AC9↔故障隔离 · AC10↔零回归）。
- [x] spec AC11（增量）有对应可观测条目（F11 ↔ 提示行 + F3 列表/轨迹 + 摘要直显）。
- [x] 人工真机清单逐项登记实际结果。（**场景 1–6 全部真机通过，2026-10-18 收口**）
