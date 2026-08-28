# Agent Loop 测试规格（CI/CD 测试脚本依据）

> 本文档提炼 spec/plan/task/checklist 四份文档，明确本阶段「要实现的功能」与「如何验证」，
> 作为后续 CI/CD 测试脚本的**唯一事实来源**。测试脚本应只依据本文档第 3、4、5 节编写。

## 1. 阶段概述

本阶段给 CodePilot 装上 **Agent Loop（ReAct 循环）**：从「单轮工具调用」升级为「自主多轮 Agent」。
核心改动集中在 `codepilot/agent.py`（单轮 → 循环编排），其余层做事件/属性扩展。

## 2. 本阶段交付的功能清单

### 2.1 功能需求

| ID | 功能 | 行为描述 |
|----|------|---------|
| F1 | ReAct 循环 | 反复「调用模型 → 执行工具 → 回灌结果」，直到模型不再请求工具 |
| F2 | 停止条件 | 5 种：①模型正常完成 ②迭代上限（默认 10，可配置）③用户取消 ④连续未知工具（默认 2）⑤流出错 |
| F3 | 异步事件流 | 事件类型：文本增量 / 工具调用 / 工具结果 / Token 用量 / 进度（轮次） |
| F4 | 流式双路收集 | 文本一边实时推送界面，一边累积完整响应供判断与回灌 |
| F5 | 工具安全性分批 | 只读工具（read/glob/grep）并发；副作用工具（write/edit/run）串行（栅栏规则） |
| F6 | Plan Mode 两段式 | `/plan` 只放开只读工具出计划；`/do` 切回全工具执行 |
| F7 | Esc 取消循环 | 置取消标志 + 中断进行中的工具任务 |
| F8 | 多轮结果回灌 | 每轮工具结果按协议原生格式回灌历史，作为下一轮输入 |

### 2.2 非功能需求

| ID | 需求 |
|----|------|
| N1 | 界面不阻塞（循环/流式/工具执行期间 TUI 可滚动） |
| N2 | 迭代上限兜底，默认 10、可配置 |
| N3 | 取消即停，输入框立即恢复 |
| N4 | 现有功能不退化（上一章 58 用例继续通过） |
| N5 | 协议无关（Anthropic / OpenAI 行为一致） |
| N6 | 退出整洁（无残留异步任务） |

---

## 3. 可自动化测试项（CI/CD 必须执行）

### 3.1 环境准备

```bash
pip install -e .        # 安装 textual / httpx / pyyaml（见 pyproject.toml）
```

> 说明：单元测试不依赖 `textual`，但 `import codepilot.app` 需要它。CI 环境务必安装依赖。

### 3.2 全量单元测试（主门禁）

```bash
python -m pytest tests/ -q --tb=short
# 期望：exit code == 0，80 passed
```

### 3.3 静态检查

```bash
# 语法检查（全部源码 + 测试）
python -m py_compile codepilot/*.py codepilot/tools/*.py tests/*.py

# 导入 / 循环依赖检查
python -c "import codepilot, codepilot.agent, codepilot.chat, codepilot.config, codepilot.protocols, codepilot.tools, codepilot.app, codepilot.main"
# 期望：两者均无输出、exit code == 0
```

### 3.4 分模块测试（按功能分层，便于失败定位）

| 层 | 命令 | 覆盖需求 | 用例数 |
|----|------|---------|--------|
| Agent 编排 | `python -m pytest tests/test_agent.py -q` | F1/F2/F5/F6/F7/F8 | 15 |
| 对话层 | `python -m pytest tests/test_chat.py -q` | F3 透传 / F7 取消不写历史 | 3 |
| 协议层 | `python -m pytest tests/test_protocols.py -q` | F3 usage 事件 / 工具调用解析 | 8 |
| 配置层 | `python -m pytest tests/test_config.py -q` | N2 迭代上限可配置 | 4 |
| 工具层 | `python -m pytest tests/test_tools_*.py -q` | side_effect 标记 / 六个工具 | 50 |

---

## 4. 需求 → 测试映射

| 需求 | 验证测试 | 断言语义 |
|------|---------|---------|
| F1 多轮循环 | `TestAgentMultiRound::test_two_tool_rounds_then_text` | 两轮工具后产出 2 个 tool_result + done |
| F2 正常完成 | `TestAgentPureText::test_pure_text_passthrough` | 纯文本一轮即停 |
| F2 迭代上限 | `TestAgentStopConditions::test_iteration_limit` | max_rounds=3 后 error 含「迭代上限」 |
| F2 连续未知 | `TestAgentStopConditions::test_consecutive_unknown_tools` | 连续 2 次未知工具 → error |
| F2 流出错 | `TestAgentToolExecution::test_tool_error_handling` | 工具 error 正常透传不崩溃 |
| F3 事件流 | `TestEventPassthrough::test_usage_and_round_passthrough` | usage/round/text 三类事件透传 |
| F3 usage | `TestAnthropicToolUse::test_usage_event` / `TestOpenAIToolCalls::test_usage_event` | 两协议产出 usage，token 数正确 |
| F4 双路收集 | `TestCancelHandling::test_normal_completion_writes_history` | 最终完整文本写入历史 |
| F5 只读并发 | `TestAgentParallel::test_parallel_execution_timing` | 3×0.1s 只读 < 0.25s（并发） |
| F5 副作用串行 | `TestAgentBatching::test_side_effect_tools_serial` | 3×0.1s 副作用 ≥ 0.25s（串行） |
| F5 顺序一致 | `TestAgentBatching::test_mixed_batching_preserves_order` | 结果顺序 == 调用顺序 |
| F6 只读过滤 | `TestAgentPlanMode::test_build_tools_read_only_in_plan_mode` | 计划模式下工具声明只含 read |
| F6 提示词 | `TestAgentPlanMode::test_system_prompt_plan_mode` | 计划模式提示词含「计划模式」 |
| F7 取消中断 | `TestAgentStopConditions::test_cancel_interrupts_tool` | cancel 后 < 3s 结束（非 5s）+ cancelled 事件 |
| F7 取消不写历史 | `TestCancelHandling::test_cancel_does_not_write_history` | 历史仅含 user 消息 |
| F8 回灌 | `TestAgentHistoryInjection::test_anthropic_injection` / `test_openai_injection` | 第二轮 messages 含 tool_use / tool_result |
| N2 可配置 | `test_config.py` 全部 | agent 段解析 + 缺省默认值 |
| N5 协议无关 | `TestAgentHistoryInjection`（双协议） | 两协议注入结构各自正确 |

---

## 5. 验收标准 → 断言映射（AC1–AC19）

| AC | 需求 | 自动化 | 对应测试 / 验证 |
|----|------|:---:|-----------------|
| AC1 | F1 多轮自动完成 | ✅ | `TestAgentMultiRound` |
| AC2 | F2 单轮即停 | ✅ | `TestAgentPureText` |
| AC3 | F2 迭代上限 | ✅ | `TestAgentStopConditions::test_iteration_limit` |
| AC4 | F2 连续未知工具 | ✅ | `TestAgentStopConditions::test_consecutive_unknown_tools` |
| AC5 | F2 流出错 | ✅ | `TestAgentToolExecution::test_tool_error_handling` |
| AC6 | F2/F7 Esc 取消 | ✅ | `TestAgentStopConditions::test_cancel_interrupts_tool` |
| AC7 | F3 事件流完整 | ✅ | `TestEventPassthrough` + `test_protocols.py` |
| AC8 | F4 双路收集 | ⚠️ 部分 | 历史完整性已测；**实时渲染需真机** |
| AC9 | F5 分批 | ✅ | `TestAgentParallel` + `TestAgentBatching` |
| AC10 | F6 /plan 拒绝 write/run | ⚠️ 部分 | 工具声明过滤已测；**模型真实调用被拒需真机** |
| AC11 | F6 停留只读态直到 /do | ⚠️ 人工 | 需真机 `/plan` → `/do` 交互 |
| AC12 | F8 回灌 | ✅ | `TestAgentHistoryInjection` |
| AC13 | N1 界面不阻塞 | ⚠️ 人工 | 需真机滚动对话区 |
| AC14 | N4 不退化 | ✅ | 全量 80 passed（含上一章 58） |
| AC15 | N2 可配置 | ✅ | `test_config.py` |
| AC16 | F3 token 用量展示 | ⚠️ 部分 | usage 事件透传已测；**状态栏渲染需真机** |
| AC17 | E2E 读文件 | ⚠️ 人工 | 需真实 API key |
| AC18 | E2E 改代码验证 | ⚠️ 人工 | 需真实 API key |
| AC19 | E2E /plan→/do | ⚠️ 人工 | 需真实 API key |

> 图例：✅ 全自动（CI 执行） ｜ ⚠️ 部分/人工（CI 不执行，见第 6 节）

---

## 6. 需人工 / 真机测试项（CI/CD **不执行**）

以下项依赖真实 API key 或交互式 TUI，单元测试无法覆盖，须在本地 `python -m codepilot` 手工验收：

| 项 | 场景 | 验收标准 |
|----|------|---------|
| M1 | 应用启动 | `python -m codepilot` 进入 TUI 无报错 |
| M2 | TUI 实时渲染 | 流式逐字 + 工具行 `● ToolName(params)` + 完成摘要 |
| M3 | 状态栏显示 | 循环中显示「第 N/M 轮」+ token 用量 |
| M4 | Esc 取消 | 循环中按 Esc → 「已取消」→ 输入框立即恢复 |
| M5 | Plan Mode | `/plan` 后发「帮我写文件」→ 模型只读探索并拒绝写；`/do` 后放开 |
| M6 | 界面不阻塞 | 工具执行期间对话区可滚动 |
| M7 | 退出整洁 | Ctrl+C / 完成后终端回显正常 |
| M8 | E2E 多轮 | 发「读 app.py 并总结」→ 自动完成无需催促 |
| M9 | E2E 改+验 | 发「改 app.py 的 X 并跑测试」→ 多轮 edit/run 直至完成 |
| M10 | E2E 死循环兜底 | 构造持续要工具的任务 → 达上限自动停止 |

---

## 7. CI/CD 测试脚本骨架（参考实现）

```bash
#!/usr/bin/env bash
set -euo pipefail

# 1. 环境准备
pip install -e . >/dev/null

# 2. 静态检查（语法 + 循环依赖）
python -m py_compile codepilot/*.py codepilot/tools/*.py tests/*.py
python -c "import codepilot, codepilot.agent, codepilot.chat, codepilot.config, \
                 codepilot.protocols, codepilot.tools, codepilot.app, codepilot.main"

# 3. 全量单元测试（主门禁，exit code 非 0 即失败）
python -m pytest tests/ -q --tb=short

# 4.（可选）分模块测试，用于快速定位失败层
#    python -m pytest tests/test_agent.py tests/test_protocols.py tests/test_chat.py
#    python -m pytest tests/test_config.py tests/test_tools_*.py

echo "ALL CHECKS PASSED"
```

> CI 只覆盖第 3、4 节与第 5 节中标记 ✅ 的项；第 6 节的 M1–M10 由人工在发布前逐条确认。
