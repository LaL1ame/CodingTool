# CodePilot Agent Loop Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 修改 | `codepilot/protocols.py` | `Usage` 数据类 + `Delta` 扩展 + 两协议产出 usage |
| 修改 | `codepilot/tools/base.py` | `Tool.side_effect` 属性（默认 False） |
| 修改 | `codepilot/tools/write.py` | `side_effect = True` |
| 修改 | `codepilot/tools/edit.py` | `side_effect = True` |
| 修改 | `codepilot/tools/run.py` | `side_effect = True` |
| 修改 | `codepilot/tools/registry.py` | `list_read_only()` |
| 修改 | `codepilot/config.py` | `AgentConfig` + `agent:` 段解析 |
| 修改 | `codepilot/agent.py` | 循环编排器重写（核心） |
| 修改 | `codepilot/chat.py` | system 组装 + 新事件透传 + 取消处理 |
| 修改 | `codepilot/app.py` | Esc 绑定、`/plan` `/do` 解析、新事件渲染 |
| 修改 | `codepilot/main.py` | 装配 `AgentConfig` |
| 修改 | `config.example.yaml` | `agent:` 段示例 |
| 新建 | `tests/test_config.py` | `agent:` 段解析测试 |
| 新建 | `tests/test_chat.py` | 新事件透传 + 取消不写历史 |
| 修改 | `tests/test_agent.py` | 脚本化 mock + 循环/分批/停止/取消用例 |
| 修改 | `tests/test_protocols.py` | usage 事件用例 |
| 修改 | `tests/test_tools_*.py` | `side_effect` 断言 |

---

## T1: 协议数据扩展（Usage + Delta 字段）

**文件：** `codepilot/protocols.py`
**依赖：** 无

**步骤：**
1. 新增 `@dataclass class Usage`，含 `input_tokens: int`、`output_tokens: int`
2. `Delta` 新增四个带默认值字段：`usage: Usage | None = None`、`round: int | None = None`、`max_rounds: int | None = None`、`cancelled: bool = False`

**验证：** `python -c "from codepilot.protocols import Delta, Usage; assert Delta(usage=Usage(1,2)).usage.output_tokens == 2; assert Delta(cancelled=True).cancelled"`

---

## T2: Anthropic 流产出 usage

**文件：** `codepilot/protocols.py` + `tests/test_protocols.py`
**依赖：** T1

**步骤：**
1. `AnthropicProtocol` 的 `message_stop` 分支：先读 `ev.get("usage")`，若存在则 `yield Delta(usage=Usage(...))`，再照原逻辑 `yield Delta(tool_calls=...)` 或 `yield Delta(done=True)`
2. `tests/test_protocols.py` 新增 mock SSE（`message_stop` 带 `usage`），断言产出含 `usage` 事件且 `input_tokens/output_tokens` 正确

**验证：** `python -m pytest tests/test_protocols.py -v`

---

## T3: OpenAI 流产出 usage

**文件：** `codepilot/protocols.py` + `tests/test_protocols.py`
**依赖：** T1

**步骤：**
1. `OpenAIProtocol` 请求体加 `body["stream_options"] = {"include_usage": True}`
2. 在 `choices = ev.get("choices", [])` 之后、`if not choices: continue` **之前**，检查 `ev.get("usage")` 并 `yield Delta(usage=...)`（否则 usage 所在空 choices 块会被跳过）
3. 新增 OpenAI usage mock 用例

**验证：** `python -m pytest tests/test_protocols.py -v`

---

## T4: Tool.side_effect 属性

**文件：** `codepilot/tools/base.py`
**依赖：** 无

**步骤：**
1. `Tool` 基类新增 property `side_effect`，返回 `False`（默认只读），带 docstring「有副作用 → 串行执行」

**验证：** `python -c "from codepilot.tools import ReadTool; from pathlib import Path; assert ReadTool(Path('.')) .side_effect is False"`

---

## T5: write / edit / run 标记副作用

**文件：** `codepilot/tools/write.py`、`codepilot/tools/edit.py`、`codepilot/tools/run.py`
**依赖：** T4

**步骤：**
1. 三个工具各自覆盖 `side_effect` property 返回 `True`
2. 在 `tests/test_tools_write.py` / `test_tools_edit.py` / `test_tools_run.py` 各加一条 `side_effect is True` 断言

**验证：** `python -m pytest tests/test_tools_write.py tests/test_tools_edit.py tests/test_tools_run.py -v`

---

## T6: ToolRegistry.list_read_only()

**文件：** `codepilot/tools/registry.py`
**依赖：** T4

**步骤：**
1. 新增 `list_read_only() -> list[Tool]`，返回 `side_effect is False` 的工具子集
2. `tests/test_tools_registry.py` 加断言：注册读写混合工具后，`list_read_only()` 只含只读工具

**验证：** `python -m pytest tests/test_tools_registry.py -v`

---

## T7: AgentConfig 配置

**文件：** `codepilot/config.py` + `config.example.yaml` + `tests/test_config.py`（新建）
**依赖：** 无

**步骤：**
1. 新增 `@dataclass class AgentConfig`，字段 `max_rounds: int = 10`、`max_unknown: int = 2`
2. `load_config` 解析可选 `agent:` 段（不存在则用默认值），并在返回值中携带（如返回 `(providers, agent_config)` 或新增 `load_config` 变体——保持向后兼容）
3. `config.example.yaml` 增加 `agent:` 段示例
4. 新建 `tests/test_config.py`：无 `agent:` 段 → 默认值；有 → 正确读取

**验证：** `python -m pytest tests/test_config.py -v`

---

## T8: Agent 状态与方法 + chat.py 组装 system

**文件：** `codepilot/agent.py` + `codepilot/chat.py`
**依赖：** T4、T6、T7

**步骤：**
1. `Agent.__init__` 新增关键字参数 `max_rounds=10`、`max_unknown=2`，存为实例属性
2. 新增 `set_plan_mode(on: bool)`：置 `self._plan_mode`
3. 新增 `cancel()`：置一个 `asyncio.Event`（本轮先实现标志，任务取消在 T11 完善）
4. 把 `SYSTEM_PROMPT` 常量从 `chat.py` 移入 `agent.py`；新增 `system_prompt()` 方法：普通模式返回基础提示词，计划模式追加「只读规划」提醒
5. `_build_tools()`：计划模式下只返回只读工具声明（用 `list_read_only()`）
6. `chat.py`：删除 `SYSTEM_PROMPT` 常量，`build_context()` 改为调用 `self.agent.system_prompt()` 组装 system 消息

**验证：** `python -m pytest tests/ -v` —— 现有 58 测试全部通过（本任务为等价重构，`run()` 尚未改循环）

---

## T9: run() 多轮循环（happy path）

**文件：** `codepilot/agent.py` + `tests/test_agent.py`
**依赖：** T8

**步骤：**
1. 把 `_MockProvider` 改成**脚本化**：构造时接收 `rounds`（每轮一组 delta 列表），每次 `stream()` 调用返回下一轮
2. `run()` 重写为循环：`for round in 1..max_rounds`：
   - `yield Delta(round=round, max_rounds=max_rounds)`
   - 逐轮流式**消费到结束**（不再 break 在 tool_calls）：累积 `text_parts`、收集 `tool_calls`、透传 `text/usage` 事件
   - 流结束判定：无 `tool_calls` → 回灌助手文本 → `yield Delta(done=True)` → return
   - 有 `tool_calls` → 回灌助手 tool_use → 执行（本任务先复用 `_execute_parallel` 全并发）→ `yield Delta(tool_result=...)` → 回灌结果 → 进入下一轮
3. 新增测试：一轮纯文本 → done；两轮（先工具后文本）→ 完整 tool_result + done

**验证：** `python -m pytest tests/test_agent.py -v`

---

## T10: 分批执行（栅栏规则）

**文件：** `codepilot/agent.py` + `tests/test_agent.py`
**依赖：** T9

**步骤：**
1. 新增 `_execute_batched(tool_calls)`：按原始顺序划分——副作用工具为单例「栅栏」组（串行），栅栏间的连续只读工具为一组（组内并发）；组按顺序执行
2. 用 `_execute_batched` 替换 `_execute_parallel`
3. 新增测试：mock 单轮 `[read, write, read]`（read/grep/glob 带延迟、write 带延迟），断言只读并发（耗时 < 串行总和）、副作用串行、结果顺序与调用顺序一致

**验证：** `python -m pytest tests/test_agent.py -v`

---

## T11: 停止条件（连续未知工具 / 迭代上限 / 取消）

**文件：** `codepilot/agent.py` + `tests/test_agent.py`
**依赖：** T9、T10

**步骤：**
1. 连续未知工具计数：一轮内出现未知工具调用则计数 +1，否则归零；计数 ≥ `max_unknown` → `yield Delta(error=...)` 并停止
2. 达 `max_rounds` 后 → `yield Delta(error="达到迭代上限...")` 并停止
3. `cancel()` 完善：置 `_cancel_event`；循环在「流式结束后」「工具执行后」检查标志 → `yield Delta(cancelled=True)` 并停止；取消时中断进行中的工具任务
4. 新增三组测试：脚本化「每轮都未知工具」→ 2 轮后 error；「每轮都要工具」→ 达上限 error；循环中途 `cancel()` → cancelled

**验证：** `python -m pytest tests/test_agent.py -v`

---

## T12: chat.py 新事件透传 + 取消不写历史

**文件：** `codepilot/chat.py` + `tests/test_chat.py`（新建）
**依赖：** T9

**步骤：**
1. `send_message` 透传 `usage`、`round/max_rounds` 事件（显式 `yield d`，否则会被现有 if 链吞掉）
2. 处理 `cancelled`：`yield d` 后 `return`，**不**把累积文本追加进 `history`
3. 新建 `tests/test_chat.py`：用 fake Agent 产出 usage/round/cancelled 事件，断言透传；取消时 `history` 无新增 assistant 消息

**验证：** `python -m pytest tests/test_chat.py -v`

---

## T13: app.py 交互（Esc 绑定 + /plan /do 解析）

**文件：** `codepilot/app.py`
**依赖：** T8

**步骤：**
1. `BINDINGS` 增加 `("escape", "cancel_loop", "取消")`
2. 新增 `action_cancel_loop`：调用 `self._agent.cancel()` 并置本地取消标志
3. `on_input_submitted` 在 `/exit` 旁拦截 `/plan` → `agent.set_plan_mode(True)`、`/do` → `set_plan_mode(False)`，写状态栏提示，不调模型

**验证：** `python -c "import codepilot.app"` 无语法/导入错误；手动 `python -m codepilot` 敲 `/plan` `/do` 观察状态切换

---

## T14: app.py 渲染新事件（usage / 进度 / 取消）

**文件：** `codepilot/app.py`
**依赖：** T12、T13

**步骤：**
1. `_process_chat` 处理 `d.usage`：更新状态栏显示本轮 token
2. `d.round/max_rounds`：状态栏显示「工作中 第 N/M 轮」
3. `d.cancelled`：打印「[dim]已取消[/dim]」并走清理（finally 已处理输入恢复）

**验证：** 手动验收 —— 真实 API 下发送多轮任务，观察状态栏轮次/token 更新；循环中按 Esc 观察「已取消」与输入恢复

---

## T15: main.py 装配 AgentConfig

**文件：** `codepilot/main.py`
**依赖：** T7

**步骤：**
1. 读取配置后取出 `AgentConfig`（缺省用默认）
2. `Agent(...)` 构造时传入 `max_rounds`、`max_unknown`

**验证：** `python -c "import codepilot.main"` 无错误；`python -m codepilot` 正常启动

---

## T16: 全量回归与收尾

**文件：** 全部
**依赖：** 所有前序任务

**步骤：**
1. `python -m pytest tests/ -v` 全绿（目标 ≥ 58 + 新增用例）
2. 检查无循环依赖、无语法错误：`python -c "import codepilot; import codepilot.agent; import codepilot.chat; import codepilot.app"`

**验证：** `python -m pytest tests/ -v` 退出码 0

---

## 执行顺序

```
T1 ──► T2
  └──► T3

T4 ──► T5
  └──► T6

T7 ──────────┐
T4,T6,T7 ────┼──► T8 ──► T9 ──► T10 ──► T11
             │              │
             │              └──► T12 ──► T14
             │
             └──► T13 ──► T14
T7 ──► T15

全部 ──► T16
```
