# MewCode 工具系统 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。

## 实现完整性

- [ ] **工具协议 (F1)**: 新增一个工具只需实现 Tool 接口，无需修改 registry/protocols/agent 代码
  - 验证：创建最小 `DummyTool(Tool)` 实现 → 注册 → `registry.list_all()` 包含它 → `registry.get("dummy")` 返回正确实例

- [ ] **tools 字段注入 (F2)**: 启动后 API 请求中 `tools` 字段包含六个工具的完整声明，Anthropic/OpenAI 格式各自符合原生规范
  - 验证：mock HTTP 拦截 → 检查 `body["tools"]` 结构和字段完整

- [ ] **System Prompt (F4)**: 请求中 system prompt 包含 Agent 角色说明和工具使用约定
  - 验证：mock HTTP 拦截 → 检查 `messages[0].content` 包含 agent/tool/coding assistant 等关键词

## 六个核心工具

- [ ] **ReadTool (F3)**: 读取文件返回 cat -n 格式；支持 offset/limit；文件不存在返回 error；越界路径返回 error
  - 验证：`python -m pytest tests/test_tools_read.py -v` 全部通过

- [ ] **WriteTool (F5)**: 写入新文件 + 自动建父目录；越界拒绝；已存在文件覆盖成功
  - 验证：`python -m pytest tests/test_tools_write.py -v` 全部通过

- [ ] **EditTool (F6)**: 唯一匹配替换成功；零匹配返回 error 含"未找到匹配文本"；多处匹配返回 error 含数量和上下文
  - 验证：`python -m pytest tests/test_tools_edit.py -v` 全部通过

- [ ] **GlobTool (F7)**: 返回按 mtime 排列的匹配文件列表；超 500 条截断；跳过 `.git`
  - 验证：`python -m pytest tests/test_tools_glob.py -v` 全部通过

- [ ] **GrepTool (F8)**: 返回匹配文件和行号；支持 ignore_case；超 250 条截断；跳过 `.git`
  - 验证：`python -m pytest tests/test_tools_grep.py -v` 全部通过

- [ ] **RunTool (F9)**: 批准 → 执行 → 返回 stdout/stderr/exit_code；拒绝 → error；超时 → kill + error
  - 验证：`python -m pytest tests/test_tools_run.py -v` 全部通过

## 协议流式解析

- [ ] **Anthropic 工具解析 (F10)**: Mock SSE（含 tool_use block + text_delta + thinking_delta）→ ToolCall 正确产出，thinking 不出现
  - 验证：`tests/test_protocols.py` 新增工具调用用例全部通过

- [ ] **OpenAI 工具解析 (F11)**: Mock 交错 tool_call delta 流 → 按 index 正确拆分多个 ToolCall
  - 验证：`tests/test_protocols.py` 新增工具调用用例全部通过

- [ ] **跨协议一致 (F12)**: 等效 Anthropic SSE 和 OpenAI SSE → Agent 收到相同结构的 ToolCall 列表
  - 验证：对比两种协议 mock 流产生的 ToolCall

## 工具执行与错误处理

- [ ] **超时保护 (F13)**: 超长执行 → 超时后返回 `ToolResult(error=...)` 而非异常；各工具用各自超时值
  - 验证：mock sleep 工具 → 超时返回 error → 会话继续

- [ ] **结构化错误 (F14)**: 文件不存在/权限不足/参数无效/超时 → 均返回 `ToolResult(error=...)`
  - 验证：逐一触发各类错误 → 检查返回值结构含 `error` 字段

## Agent 编排

- [ ] **结果回灌 (F15)**: 工具执行后 → 下一轮 API messages 中包含正确格式的 tool_result
  - 验证：mock provider → 检查第二轮请求的 messages 结构

- [ ] **并行执行 (F16)**: 3 个工具调用 → 并发执行 → 总耗时 ≈ 最慢工具时间（非三者之和）
  - 验证：注入不同延迟的 mock 工具 → assert 总耗时 < 串行总和

## TUI 展示

- [ ] **工具行 (F17)**: 工具调用时出现 `● ToolName(params)` → 完成后更新摘要 → 失败显示 `✕ Error: ...`
  - 验证：启动应用 → 发送需工具调用的消息 → 观察工具行的出现和状态变化（需真实 API key）

- [ ] **命令确认 (F18)**: run 工具弹出确认列表（三选项：批准/拒绝/始终允许），↑↓ 切换，Enter 确认，数字键直达
  - 验证：发送 "列出当前目录文件" → 分别测试批准、拒绝、"始终允许"后自动跳过确认（需真实 API key）

## 非功能需求

- [ ] **界面不阻塞 (N1)**: 工具执行期间对话区可正常滚动
  - 验证：启动长搜索 → 在结果返回前滚动对话历史 → 流畅（需真实 API key）

- [ ] **工作目录边界 (N6)**: read/write/edit/glob/grep 对越界路径和绝对路径返回 error
  - 验证：用越界路径调用每个工具 → 均返回 "超出工作目录"（单元测试覆盖）

- [ ] **现有功能不退化 (N9)**: 发送纯文本 "Hello" → 流式逐字 + Markdown 渲染 + 响应计时与 MVP 一致
  - 验证：启动应用 → 发送纯文本 → 行为与 MVP 无差异

- [ ] **体量控制 (N10)**: read >2000 行截断；grep >250 条截断；run >8000 字符截断 — 均带 `[truncated]` 标记
  - 验证：准备超长文件/高频搜索/长输出 → 触发各工具 → 检查截断标记（单元测试覆盖）

## 集成检查

- [ ] `mewcode.tools` 子包可独立导入
  - 验证：`python -c "from mewcode.tools import Tool, ToolRegistry, ReadTool; print('OK')"`

- [ ] `mewcode.agent` 导入不触发循环依赖
  - 验证：`python -c "from mewcode.agent import Agent; print('OK')"`

- [ ] `python -m mewcode` 可正常启动（导入无错误）
  - 验证：`python -c "import mewcode; print('OK')"`

## 验证与测试

- [ ] 全量测试通过（目标：≥ 50 个测试用例）
  - 验证：`python -m pytest tests/ -v` 退出码为 0

- [ ] MVP 回归：之前的 31 个测试（如有）全部继续通过
  - 验证：协议层/Provider层/对话管理层的原有行为不受影响

## 端到端场景

- [ ] **E2E-1: Anthropic 读文件** — 发送 "帮我读一下 app.py" → `● read(app.py)` → 结果显示 → 模型分析
  - 验收：全流程无崩溃，工具行正确展示，模型回复有依据
  - 条件：需 Anthropic API key

- [ ] **E2E-2: OpenAI 写文件** — 发送 "创建 hello.py 写入 print('hi')" → `● write(hello.py)` → 文件创建 → 模型确认
  - 验收：文件内容正确，工具行展示 `● Wrote(N bytes)`
  - 条件：需 OpenAI API key 或兼容端点

- [ ] **E2E-3: 命令拒绝** — 发送 "执行 dir 命令" → 弹确认框 → 拒绝 → 模型回应 "好的，不执行"
  - 验收：会话不中断，模型正常应对拒绝

- [ ] **E2E-4: 并行工具调用** — 发送 "同时读 app.py 和 chat.py" → 两个工具行同时出现 → 几乎同时完成
  - 验收：两个工具行可见，回复基于两个文件

- [ ] **E2E-5: Edit 失败重试** — 发送 "帮我改 app.py 里的 XXX"（不存在）→ error → 模型调整参数重试
  - 验收：模型能根据错误信息调整调用

- [ ] **E2E-6: 超时恢复** — 发送 "执行 sleep 999" → 超时后 `✕ Error: 超时` → 模型正常应对
  - 验收：会话不卡死

> 注：标记为"需真实 API key"的条目无法通过单元测试覆盖，需在终端中运行 `python -m mewcode` 进行人工验收。
> MVP checklists（配置加载、TUI 布局、Markdown 渲染等）在本期继续适用，此处不重复列出。
