# CodePilot Agent 停止判定增强 Checklist

> 基于已批准 spec.md + plan.md + task.md。聚焦系统行为，可运行/观察验证。

## 实现完整性
- [ ] **Delta** 带 paused/pause_reason/stall_hint/resume_round 字段（验证：`python -c "from codepilot.protocols import Delta; d=Delta(); assert d.paused is False and d.stall_hint is None"`）。
- [ ] **AgentConfig** 默认 max_rounds=18 + 空转参数（验证：`load_agent_config` 返回默认 18）。
- [ ] **_StallDetector** 挂载于 Agent（验证：单测 import 可用）。

## 功能/验收（AC）
- [ ] **AC-F1**：默认 max_rounds=18；config 覆盖生效（验证：`test_config` 读 AgentConfig）。
- [ ] **AC-F2**：达上限未完成 → paused(reason="limit") 而非 error；同一 `_msgs` 再 run 续跑、历史不丢（验证：`test_agent_resume.py`）。
- [ ] **AC-F3**：重复调用且无文本进展 → stall_hint；持续 → paused(reason="stall")；「慢但前进」（有文本/不同参数）不触发（验证：`test_agent` 空转单测）。
- [ ] **AC-F4**：停止 reason 结构化 done/limit/stall/unknown_tool/cancelled/error 可区分（验证：单测断言 Delta.pause_reason/done）。
- [ ] **AC-F5**：TUI 收 paused 显示「输入 /continue 续跑」；/continue 沿用同一 `_msgs` 续跑；stall_hint 显示提示（验证：端到端观察）。
- [ ] **AC-N1**：paused/stall_hint 在 UI 不显示为红色 error（验证：`_process_chat` 处理逻辑 + 端到端观察）。
- [ ] **AC-N2**：续接不重放副作用——副作用工具执行次数不重复（验证：`test_agent_resume.py` 断言执行计数）。
- [ ] **AC-N4**：空转阈值经 config 可调（验证：注入不同 stall_window/repeat_k 单测行为变化）。
- [ ] **AC-N5**：新 Delta 字段默认 None/False，旧消费方不受影响（验证：全量回归绿）。

## 集成
- [ ] 停止原因 → UI 分派正确（done/limit/stall/unknown_tool/cancelled/error 各显示对应文案，不混淆）（验证：单测/端到端）。
- [ ] /continue 命令经 parse_command 识别并路由（验证：`test_commands.py`）。

## 验证与测试
- [ ] 项目无语法错误、导入成功（验证：`python -m compileall codepilot/agent.py codepilot/protocols.py codepilot/ui/app.py`）。
- [ ] 全量单测通过、无回归（验证：`pytest -q` → 既有 256 + 新增全绿）。

## 端到端场景
- [ ] **场景1（超限续接）**：给一个需 >18 轮的多步任务（或临时改 max_rounds=3 模拟）→ 跑满暂停显示「输入 /continue 续跑」，输入 `/continue` 后能继续完成（验证：TUI 真机）。
- [ ] **场景2（空转提示）**：诱导模型重复同一 read（反复读同一文件同段）→ 出现「检测到可能空转，已建议换策略」，若持续则「检测到持续空转已暂停」（验证：TUI）。
- [ ] **场景3（不误伤）**：正常多阶段任务（检索+编辑+执行）→ 不误报空转、正常完成（验证：TUI 端到端）。

## 已人工验证/备注（开发后填写）
- [ ] 待验收