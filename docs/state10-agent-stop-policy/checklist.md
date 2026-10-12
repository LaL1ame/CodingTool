# CodePilot Agent 停止判定增强 Checklist

> 基于已批准 spec.md + plan.md + task.md。聚焦系统行为，可运行/观察验证。

## 实现完整性 ✅
- [x] **Delta** 带 paused/pause_reason/stall_hint/resume_round 字段（验证：`codepilot/protocols.py` + `test_agent_resume.py` 断言 paused/pause_reason/resume_round/stall_hint）。
- [x] **AgentConfig** 默认 max_rounds=18 + 空转参数（验证：`codepilot/config.py` 默认 18；`load_agent_config` 返回默认）。
- [x] **_StallDetector** 挂载于 Agent（验证：`codepilot/agent.py` + `test_agent_resume.py::TestStallDetection`）。

## 功能/验收（AC）✅
- [x] **AC-F1**：默认 max_rounds=18；config 覆盖生效（验证：`test_config` + 真机 config 临时 `agent: max_rounds: 3` 生效，日志显示「round 1/3」）。
- [x] **AC-F2**：达上限未完成 → paused(reason="limit") 而非 error；同一 `_msgs` 再 run 续跑、历史不丢（验证：`test_agent_resume.py::TestLimitPause` + 真机场景1，日志 `round 3/3 … stop: iteration limit 3 reached` → `/continue` → round 1/3 done）。
- [x] **AC-F3**：重复调用且无文本进展 → stall_hint；持续 → paused(reason="stall")；「慢但前进」（有文本/不同参数）不触发（验证：`test_agent_resume.py::TestStallDetection` 全绿；场景3 真机多阶段无空转误报）。
- [x] **AC-F4**：停止 reason 结构化 done/limit/stall/unknown_tool/cancelled/error 可区分（验证：`test_agent.py`/`test_agent_resume.py` 断言 pause_reason/done）。
- [x] **AC-F5**：TUI 收 paused 显示「输入 /continue 续跑」；/continue 沿用同一 `_msgs` 续跑；stall_hint 显示提示（验证：真机场景1，`/continue` 续跑完成）。
- [x] **AC-N1**：paused/stall_hint 在 UI 不显示为红色 error（验证：`_process_chat` 用 dim 文案非 red + 真机观察）。
- [x] **AC-N2**：续接不重放副作用——副作用工具执行次数不重复（验证：`test_agent_resume.py::TestResumeNoSideEffectReplay`；真机续接未重读任何文件）。
- [x] **AC-N4**：空转阈值经 config 可调（验证：`AgentConfig.stall_*` 注入不同 repeat_k/window 单测行为变化）。
- [x] **AC-N5**：新 Delta 字段默认 None/False，旧消费方不受影响（验证：全量 264 回归绿）。

## 集成 ✅
- [x] 停止原因 → UI 分派正确（验证：`ui/app.py::_process_chat` 分支 + 真机 limit/stall 文案区分，均 dim 非 red）。
- [x] /continue 命令经 parse_command 识别并路由（验证：`test_commands.py::test_continue_alone`）。

## 验证与测试 ✅
- [x] 项目无语法错误、导入成功（验证：imports 冒烟 OK）。
- [x] 全量单测通过、无回归（验证：`pytest -q` → **264 passed**）。

## 端到端场景
- [x] **场景1（超限续接）**：真机——临时 `max_rounds=3` 读 3 工具文件+分析 → `round 3/3 → stop: iteration limit 3 reached` → 显示「输入 /continue 续跑」→ 输 `/continue` → `round 1/3 → done`（模型基于已读上下文直接给出最终对比，不重读）。
- [ ] **场景2（空转提示）**：真机诱导重复读未单独专测（合约已由单测 `TestStallDetection` 覆盖）；建议后续可选诱导一次。
- [x] **场景3（不误伤）**：真机——读全项目 55 个 py（高并发批量 read）正常完成无空转误报；多任务 read+grep+分析正常完成。

## 已人工验证/备注（开发后填写）
- ✅ **验收通过**（阶段六）：A(read 全量) + B(limit→/continue 续接) 真机验证完成；264 单测全绿。
- 验收中发现并修复 state9 层1契约缺陷：**read 豁免层1存盘**（自带分页）。修复 commit `a4138d7`；回归测试 `test_manager.py::test_prep_tool_result_exempts_read`。
- 备注：`config.yaml` 含真实 API key 已 gitignore；验收临时 `max_rounds: 3` 已删除，恢复默认 18。