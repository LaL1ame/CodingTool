# CodePilot Agent 停止判定增强 Tasks

> 基于已批准 spec.md + plan.md。改动集中在 agent 停止判定 + Delta + UI/续接。

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 修改 | `codepilot/protocols.py` | Delta 加 paused/pause_reason/stall_hint/resume_round |
| 修改 | `codepilot/config.py` | AgentConfig max_rounds=18 + 空转参数 |
| 修改 | `codepilot/agent.py` | 停止判定重构 + _StallDetector |
| 修改 | `codepilot/commands.py` | /continue 解析 |
| 修改 | `codepilot/chat.py` | 续接沿 _msgs |
| 修改 | `codepilot/ui/app.py` | paused/stall 展示 + /continue 处理 |
| 新建 | `tests/test_agent_resume.py` | N2 续接集成测试 |
| 修改 | `tests/test_agent.py` | F2/F3/F4 停止判定单测 |
| 修改 | `tests/test_commands.py` | /continue 解析单测 |

## T1: Delta 协议字段（N5 兼容）

**文件：** `codepilot/protocols.py`
**依赖：** 无
**步骤：**
1. `Delta` dataclass 末尾加 `paused: bool = False`、`pause_reason: str | None = None`、`stall_hint: str | None = None`、`resume_round: int | None = None`（默认 None/False，保证旧消费方忽略）。
**验证：** `tests/test_protocols.py` 既有测试仍绿（新字段默认值不破坏）；`Delta()` 可构造。

## T2: AgentConfig 增量

**文件：** `codepilot/config.py`
**依赖：** 无
**步骤：**
1. `AgentConfig` `max_rounds` 默认 10 → **18**。
2. 新增 `stall_enabled/stall_repeat_k/stall_window/stall_msg`（默认 True/2/3/提示文本）。
3. `load_agent_config` 从 YAML 读这些字段。
**验证：** `tests/test_config.py` 有 AgentConfig 相关单测则更新断言默认 18；`load_agent_config` 注入覆盖生效。

## T3: _StallDetector（F3）

**文件：** `codepilot/agent.py`
**依赖：** T1
**步骤：**
1. 新增 `_StallDetector` 类：`record(tool_calls, had_text) -> bool`、`should_stop() -> bool`、`_fingerprint(tc)`（name + repr(arguments) hash）。
2. `record`：无工具调用 → 不判空转；`had_text=True` → 有文本进展，重置重复计数；否则按指纹在窗口内计数，`>= repeat_k` 返回 True。
3. `should_stop`：连续窗口内重复指纹的比例/次数超阈值 → True（持续空转硬停）。
**验证：** `test_agent.py` 新单测：同签名重复且无文本 → record 返回 True；有文本 → 不触发；不同参数 → 不触发。

## T4: 停止判定重构（F2/F4）

**文件：** `codepilot/agent.py`
**依赖：** T2,T3
**步骤：**
1. `run()` 里 `Agent` 构造时实例化 `_StallDetector`（读 AgentConfig stall 参数）。
2. 改 line 161：`round >= max_rounds` 从 `yield Delta(error=...)` 改为 `yield Delta(paused=True, pause_reason="limit", resume_round=round_num, max_rounds=max_rounds)`。
3. 改 `consecutive_unknown` 停止：从 error 改 paused(pause_reason="unknown_tool") 且带提示文本（N1）。
4. 停轮回灌前调 `detector.record`：返回 True 先 `yield Delta(stall_hint=msg)`（继续跑）；随后 `should_stop()` 硬停 `yield Delta(paused=True, pause_reason="stall")`。
**验证：** `test_agent` 断言：`_MockProvider` 提供多轮持续工具调用，跑满 max_rounds 得到 paused(reason=limit) 而非 error；空转场景得到 stall_hint→paused(stall)。

## T5: /continue 命令解析

**文件：** `codepilot/commands.py`
**依赖：** 无
**步骤：**
1. `parse_command` 加 `if first == "/continue": return ("continue", "")`，docstring 更新。
**验证：** `test_commands.py` 加断言。

## T6: 续接支持（chat）

**文件：** `codepilot/chat.py`
**依赖：** T4
**步骤：**
1. `send_message` 保持 `_msgs` 持久（已成）；确认续接时不新增 user 消息逻辑——需区分「新消息」与「续接」。加一个 `send_message(content, resume=False)`：`resume=True` 时跳过追加 user 消息到 `_msgs`/history，直接沿用构建 ctx。
**验证：** （在 test_agent_resume 覆盖）续接后 `_msgs` 无新增 user 空消息、历史完整。

## T7: UI 展示 + /continue 处理（F5/N1）

**文件：** `codepilot/ui/app.py`
**依赖：** T5,T6
**步骤：**
1. `_process_chat` 处理 `d.paused`：reason=limit → dim 提示「已达迭代上限(N),输入 /continue 续跑」；reason=stall → 「检测到持续空转,已暂停」；reason=unknown_tool → 友好提示。均 return 不显示 error。
2. `d.stall_hint` → dim 提示「检测到可能空转,已建议换策略」。
3. `on_input_submitted`/`_process_chat` 加 `/continue` 分支：`await self._chat.send_message("", resume=True)` 延续（不新增 user 显示）。
**验证：** 端到端（checklist）观察 paused/stall 显示；`_process_chat` 收到 paused 不写红色 error。

## T8: 测试完善

**文件：** `tests/test_agent_resume.py`、`tests/test_agent.py`
**依赖：** T4–T7
**步骤：**
1. 新建 `tests/test_agent_resume.py`：
   - 达上限 → paused(limit)；用同一 `_msgs` 再 `run()` 能续跑（已完成轮次与历史保留）。
   - N2：续接不重放副作用——副作用工具在续接中执行次数不重复。
2. 补 `test_agent` 空转单测（同签名重复、文本进展豁免、stall_hint→paused 链）。
**验证：** `pytest test_agent_resume.py test_agent.py -q` 全绿。

## 执行顺序
```
T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8
（T5 无依赖可在 T2 后并行；T8 最后统一补测。）
```