# CodePilot Agent Loop Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。
> 标记「需真实 API key」的条目无法用单元测试覆盖，需在终端 `python -m codepilot` 人工验收。

## 循环与停止条件（F1 / F2）

- [ ] **多轮循环自动完成 (F1)**: 发送需多步工具调用的任务，Agent 自动循环直到模型不再请求工具，无需逐轮催促
  - 验证：`python -m pytest tests/test_agent.py -v` 多轮用例通过（mock 两轮：先工具后文本 → 完整 done）

- [ ] **纯文本单轮即停 (F2)**: 模型直接回复文本、不请求工具时，循环一轮即结束，行为与上一章一致
  - 验证：`python -m pytest tests/test_agent.py -v` 纯文本用例通过

- [ ] **迭代上限兜底 (F2)**: 模型每轮都请求工具时，达到上限（默认 10）后停止并提示
  - 验证：脚本化 mock 每轮都产出工具调用 → 断言 error 含「迭代上限」

- [ ] **连续未知工具停止 (F2)**: 连续 2 轮请求不存在的工具 → 停止并报错
  - 验证：脚本化 mock 每轮产出未知工具调用 → 断言 error 含「未知工具」

- [ ] **流出错停止 (F2)**: 流式响应返回 HTTP 错误 → 停止并显示错误
  - 验证：mock provider 产出 error delta → 断言循环停止、error 透传

## 事件流与双路收集（F3 / F4）

- [ ] **事件流完整 (F3)**: 一次多轮对话中，事件流依次产出 文本 / 工具调用 / 工具结果 / 进度（轮次）/ Token 用量 五类事件
  - 验证：`tests/test_agent.py` 收集全部 delta，断言五类事件齐全

- [ ] **usage 事件 (F3)**: 每轮结束产出 Token 用量，Anthropic 与 OpenAI 均覆盖
  - 验证：`python -m pytest tests/test_protocols.py -v` usage 用例通过

- [ ] **双路收集 (F4)**: 流式期间界面实时显示增量文本，同时最终完整回复保存进历史
  - 验证：`python -m pytest tests/test_chat.py -v`（历史含完整文本）；真实 API 下观察逐字渲染

## 工具分批执行（F5）

- [ ] **只读并发 (F5)**: 单轮多个只读工具（read/glob/grep）并发执行，总耗时 ≈ 最慢者而非之和
  - 验证：`tests/test_agent.py` 时序断言 < 串行总和

- [ ] **副作用串行 (F5)**: 单轮多个副作用工具（write/edit/run）逐个串行执行，绝不并发
  - 验证：`tests/test_agent.py` 时序断言 ≈ 各自之和

- [ ] **结果顺序一致 (F5)**: 无论并发/串行，回灌结果顺序与模型调用顺序一致
  - 验证：断言 tool_result 顺序 == tool_calls 顺序

## Plan Mode（F6）

- [ ] **只读过滤 (F6)**: `/plan` 下模型只能调 read/glob/grep，调用 write/run 被拒绝并回传错误
  - 验证：mock 断言计划模式下工具声明只含只读；手动 `/plan` 后发「帮我写个文件」观察拒绝

- [ ] **两段式 (F6)**: `/plan` 输出计划后停留在只读态，直到敲 `/do` 才放开全工具
  - 验证：手动 `python -m codepilot` 敲 `/plan` → 发任务 → 观察只读 → 敲 `/do` → 观察放开

## 取消（F7 / N3）

- [ ] **Esc 取消循环 (F7)**: 循环中按 Esc 立即停止：不发起新请求、中断进行中工具、输入框恢复可用
  - 验证：`tests/test_agent.py` cancel 用例断言 `cancelled` 事件；手动循环中按 Esc 观察「已取消」

## 多轮结果回灌（F8）

- [ ] **回灌正确 (F8)**: 第二轮模型请求能看到第一轮工具结果，Anthropic/OpenAI 各自原生格式正确
  - 验证：`tests/test_agent.py` 断言第二轮请求的 messages 含上一轮 tool_result

## 非功能需求（N1–N6）

- [ ] **界面不阻塞 (N1)**: 工具执行与多轮循环期间，对话区可正常滚动
  - 验证：手动启动长任务，在结果返回前滚动对话历史（需真实 API key）

- [ ] **迭代上限可配置 (N2)**: `config.yaml` 的 `agent:` 段可改 `max_rounds`
  - 验证：`python -m pytest tests/test_config.py -v`

- [ ] **现有功能不退化 (N4)**: 上一章 58 个测试全部继续通过
  - 验证：`python -m pytest tests/ -v` 退出码 0

- [ ] **协议无关 (N5)**: 同一循环场景 Anthropic 与 OpenAI 行为一致
  - 验证：`tests/test_agent.py` 两种协议注入用例通过

- [ ] **退出整洁 (N6)**: 取消/退出后无残留异步任务，终端恢复正常
  - 验证：手动 Esc / Ctrl+C 后终端回显正常、无报错

## 集成检查

- [ ] 无循环依赖，各模块可独立导入
  - 验证：`python -c "import codepilot; import codepilot.agent; import codepilot.chat; import codepilot.app; import codepilot.main"`

- [ ] 应用可正常启动
  - 验证：`python -m codepilot` 无导入错误，进入 TUI

- [ ] Token 用量展示：界面状态栏显示每轮 token 与轮次进度
  - 验证：真实 API 下发送多轮任务，观察状态栏（需真实 API key）

## 验证与测试

- [ ] 全量测试通过（目标 ≥ 58 + 本章新增用例）
  - 验证：`python -m pytest tests/ -v` 退出码 0

- [ ] 无语法错误 / lint（如项目已配置）
  - 验证：`python -m compileall codepilot` 或 `ruff check .`（如有配置）

## 端到端场景

- [ ] **E2E-1: 多轮自动完成** — 发送「读 app.py 并总结」→ `● read(app.py)` → 模型分析 → 无需催促
  - 验收：全流程无崩溃，工具行正确，回复有依据（需 API key）

- [ ] **E2E-2: 改代码并验证** — 发送「把 app.py 的 X 改成 Y 并跑测试」→ 多轮 edit/run 循环直至完成
  - 验收：模型自主完成改+验，工具行依次出现（需 API key）

- [ ] **E2E-3: Plan Mode 两段式** — `/plan` 探索出计划 → 敲 `/do` → 执行
  - 验收：计划阶段只读，执行阶段放开（需 API key）

- [ ] **E2E-4: Esc 取消** — 循环进行中按 Esc → 「已取消」→ 输入框立即恢复
  - 验收：会话不卡死，可继续发下一条

- [ ] **E2E-5: 死循环兜底** — 构造一个持续要工具的任务 → 达迭代上限自动停止并提示
  - 验收：循环在 10 轮左右停止，不无限进行（需 API key 或 mock）

> 注：上一章（tool-system）的 MVP 回归项（配置加载、TUI 布局、Markdown 渲染、六工具行为）在本期继续适用，不重复列出，由「现有功能不退化」条目统一覆盖。
