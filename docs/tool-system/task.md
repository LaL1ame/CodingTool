# CodePilot 工具系统 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `codepilot/tools/__init__.py` | 聚合导出 |
| 新建 | `codepilot/tools/base.py` | Tool ABC + validate_path |
| 新建 | `codepilot/tools/registry.py` | ToolRegistry + 格式转换 |
| 新建 | `codepilot/tools/read.py` | ReadTool |
| 新建 | `codepilot/tools/write.py` | WriteTool |
| 新建 | `codepilot/tools/edit.py` | EditTool |
| 新建 | `codepilot/tools/glob_tool.py` | GlobTool |
| 新建 | `codepilot/tools/grep_tool.py` | GrepTool |
| 新建 | `codepilot/tools/run.py` | RunTool |
| 新建 | `codepilot/agent.py` | Agent 工具编排 |
| 修改 | `codepilot/protocols.py` | +ToolCall, ToolResult, Delta 扩展, 双协议工具解析 |
| 修改 | `codepilot/providers.py` | stream() 加 tools 参数 |
| 修改 | `codepilot/chat.py` | ChatManager 委托 Agent |
| 修改 | `codepilot/app.py` | 工具行渲染 + 命令确认列表选择 |
| 修改 | `codepilot/main.py` | 组装 Agent + Registry |
| 新建 | `tests/__init__.py` | 测试包 |
| 新建 | `tests/test_tools_base.py` | validate_path + Tool 接口 |
| 新建 | `tests/test_tools_registry.py` | 注册 + 格式转换 |
| 新建 | `tests/test_tools_read.py` | ReadTool |
| 新建 | `tests/test_tools_write.py` | WriteTool |
| 新建 | `tests/test_tools_edit.py` | EditTool |
| 新建 | `tests/test_tools_glob.py` | GlobTool |
| 新建 | `tests/test_tools_grep.py` | GrepTool |
| 新建 | `tests/test_tools_run.py` | RunTool |
| 修改 | `tests/test_protocols.py` | +工具调用解析 |
| 修改 | `tests/test_providers.py` | +tools 参数传递 |
| 新建 | `tests/test_agent.py` | Agent 编排 |

---

## Phase 1: 工具基础设施

### T1: 创建 tools/ 子包 + Tool 抽象接口

**文件：** `codepilot/tools/__init__.py`, `codepilot/tools/base.py`
**依赖：** 无
**步骤：**
1. 创建 `codepilot/tools/` 目录
2. 创建 `__init__.py`，暂为空（后续逐步添加导出）
3. 创建 `base.py`：
   - 从 `codepilot.protocols` 导入 `ToolResult`（避免循环引用）
   - 定义 `Tool` ABC：`name`(property), `description`(property), `parameters`(property), `execute(**kwargs) → ToolResult`
   - 定义 `validate_path(relative_path: str, workspace_root: Path) → Path`：
     - 解析 `workspace_root / relative_path` → `resolve()`
     - 校验 `resolved_path.is_relative_to(workspace_root)`
     - 通过返回 resolved_path，失败抛 `ValueError("路径超出工作目录: {path}")`

**验证：** `python -c "from codepilot.tools.base import Tool, validate_path; print('OK')"`

### T2: 实现 ToolRegistry

**文件：** `codepilot/tools/registry.py`
**依赖：** T1
**步骤：**
1. 创建 `registry.py`
2. 实现 `ToolRegistry` 类：
   - `__init__` → `_tools: dict[str, Tool]`
   - `register(tool: Tool)` → 按 `tool.name` 存入 `_tools`
   - `get(name: str) → Tool | None`
   - `list_all() → list[Tool]`
   - `to_anthropic_tools() → list[dict]`：每项 `{"name": t.name, "description": t.description, "input_schema": t.parameters}`
   - `to_openai_tools() → list[dict]`：每项 `{"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}`
3. 更新 `__init__.py`：导出 `Tool`, `ToolRegistry`

**验证：**
```python
from codepilot.tools import Tool, ToolRegistry
r = ToolRegistry()
assert r.get("nonexistent") is None
```

---

## Phase 2: 六个工具实现

### T3: ReadTool

**文件：** `codepilot/tools/read.py`
**依赖：** T1, T2
**步骤：**
1. 创建 `read.py`，实现 `ReadTool(Tool)`：
   - `name = "read"`, `description` 含用途和使用说明
   - `parameters` = `{"type": "object", "properties": {"file_path": {"type": "string", "description": "..."}, "offset": {"type": "integer", "default": 0}, "limit": {"type": "integer"}}, "required": ["file_path"]}`
   - `execute(file_path, offset=0, limit=None)`：
     - `validate_path(file_path, self.workspace)`
     - 读文件 → `read_text(encoding="utf-8")`
     - 按行 split → offset/limit 切片
     - cat -n 格式：`f"{lineno:>6}\t{line}"` 
     - 超过 2000 行截断 → `truncated=True, total_items=<总行数>`
     - 捕获 FileNotFoundError → `ToolResult(error="文件不存在: {path}")`
     - 默认超时 30s
2. 更新 `__init__.py`：导出 `ReadTool`

**验证：**
```python
from codepilot.tools.read import ReadTool
from pathlib import Path
import tempfile, os
d = tempfile.mkdtemp()
f = os.path.join(d, "test.txt")
Path(f).write_text("line1\nline2\nline3")
t = ReadTool(workspace=Path(d))
r = t.execute(file_path="test.txt")
assert "line1" in r.output
```

### T4: WriteTool

**文件：** `codepilot/tools/write.py`
**依赖：** T1, T2
**步骤：**
1. 创建 `write.py`，实现 `WriteTool(Tool)`：
   - `name = "write"`, `description` 含用途说明
   - `parameters` = `{"type": "object", "properties": {"file_path": {"type": "string", "description": "..."}, "content": {"type": "string", "description": "..."}}, "required": ["file_path", "content"]}`
   - `execute(file_path, content)`：
     - `validate_path` → `parent.mkdir(parents=True, exist_ok=True)`
     - `write_text(content, encoding="utf-8")`
     - 返回 `ToolResult(output=f"Wrote {len(content)} bytes to {file_path}")`
     - 超时 10s
2. 更新 `__init__.py`：导出 `WriteTool`

**验证：**
```python
from codepilot.tools.write import WriteTool
from pathlib import Path
import tempfile
d = tempfile.mkdtemp()
t = WriteTool(workspace=Path(d))
r = t.execute(file_path="sub/deep/hello.py", content="print('hi')")
assert "Wrote" in r.output
assert Path(d, "sub/deep/hello.py").read_text() == "print('hi')"
```

### T5: EditTool

**文件：** `codepilot/tools/edit.py`
**依赖：** T1, T2
**步骤：**
1. 创建 `edit.py`，实现 `EditTool(Tool)`：
   - `name = "edit"`, `description` 含唯一匹配替换说明
   - `parameters` = `{"type": "object", "properties": {"file_path": {"type": "string"}, "old_string": {"type": "string"}, "new_string": {"type": "string"}}, "required": ["file_path", "old_string", "new_string"]}`
   - `execute(file_path, old_string, new_string)`：
     - `validate_path` → 读文件
     - `count = content.count(old_string)`
     - `count == 1` → 替换 → 写回 → `ToolResult(output=f"Replaced 1 occurrence in {file_path} at line {lineno}")`
     - `count == 0` → `ToolResult(error="未找到匹配文本。请用 read 确认文件当前内容。")`
     - `count >= 2` → `ToolResult(error=f"匹配到 {count} 处。请缩小范围使匹配唯一。\n" + 每处所在行号及前后 2 行上下文)`
     - 超时 10s
2. 更新 `__init__.py`：导出 `EditTool`

**验证：**
```python
from codepilot.tools.edit import EditTool
from pathlib import Path
import tempfile, os
d = tempfile.mkdtemp()
f = os.path.join(d, "test.py")
Path(f).write_text("hello world\nfoo bar\nhello world")
t = EditTool(workspace=Path(d))
# 多处匹配 → error
r = t.execute(file_path="test.py", old_string="hello", new_string="hi")
assert r.error and "2" in r.error
# 零匹配 → error
r = t.execute(file_path="test.py", old_string="zzz", new_string="hi")
assert r.error and "未找到" in r.error
# 唯一匹配 → 成功
r = t.execute(file_path="test.py", old_string="foo bar", new_string="baz qux")
assert r.output and "Replaced" in r.output
```

### T6: GlobTool

**文件：** `codepilot/tools/glob_tool.py`
**依赖：** T1, T2
**步骤：**
1. 创建 `glob_tool.py`，实现 `GlobTool(Tool)`：
   - `name = "glob"`, `description` 含 glob 语法说明
   - `parameters` = `{"type": "object", "properties": {"pattern": {"type": "string", "description": "..."}}, "required": ["pattern"]}`
   - `execute(pattern)`：
     - `workspace.rglob(pattern)` → 收集相对路径列表
     - 跳过 `.git` 目录
     - 按 mtime 降序排序
     - 超过 500 条 → 截断 + `truncated=True, total_items=N`
     - 返回格式：每行一个路径
     - 超时 30s
2. 更新 `__init__.py`：导出 `GlobTool`

**验证：**
```python
from codepilot.tools.glob_tool import GlobTool
from pathlib import Path
t = GlobTool(workspace=Path.cwd())
r = t.execute(pattern="**/*.py")
assert ".py" in r.output or r.output == ""
```

### T7: GrepTool

**文件：** `codepilot/tools/grep_tool.py`
**依赖：** T1, T2
**步骤：**
1. 创建 `grep_tool.py`，实现 `GrepTool(Tool)`：
   - `name = "grep"`, `description` 含正则语法说明
   - `parameters` = `{"type": "object", "properties": {"pattern": {"type": "string"}, "ignore_case": {"type": "boolean", "default": False}, "path": {"type": "string"}}, "required": ["pattern"]}`
   - `execute(pattern, ignore_case=False, path=".")`：
     - `re.compile(pattern, re.IGNORECASE if ignore_case else 0)`
     - 遍历文件（跳过 `.git` 目录）→ 逐行匹配
     - 格式：`file_path:lineno:content`
     - 超过 250 条 → 截断 + `truncated=True, total_items=N`
     - 超时 30s
2. 更新 `__init__.py`：导出 `GrepTool`

**验证：**
```python
from codepilot.tools.grep_tool import GrepTool
from pathlib import Path
t = GrepTool(workspace=Path.cwd())
r = t.execute(pattern="import", path="codepilot")
assert "import" in r.output
```

### T8: RunTool

**文件：** `codepilot/tools/run.py`
**依赖：** T1, T2
**步骤：**
1. 创建 `run.py`，实现 `RunTool(Tool)`：
   - `name = "run"`, `description` 含命令执行说明
   - `parameters` = `{"type": "object", "properties": {"command": {"type": "string", "description": "..."}}, "required": ["command"]}`
   - `__init__` 额外参数 `timeout: float = 120.0`, `confirm_callback: Callable | None = None`
   - `execute(command)`：
     - 若有 `confirm_callback` → `approved = await confirm_callback(command)`
     - `not approved` → `ToolResult(error="用户拒绝执行")`
     - `approved` → `asyncio.create_subprocess_shell(command, cwd=workspace, stdout=PIPE, stderr=PIPE)`
     - `asyncio.wait_for(process.communicate(), timeout=timeout)`
     - stdout + stderr 超过 8000 字符 → 截断
     - 返回 `ToolResult(output=f"Exit code: {code}\nSTDOUT:\n{out}\nSTDERR:\n{err}")`
     - 超时 → kill 进程 → `ToolResult(error="命令超时 (120s)")`
2. 更新 `__init__.py`：导出 `RunTool`

**验证：**
```python
from codepilot.tools.run import RunTool
from pathlib import Path
t = RunTool(workspace=Path.cwd(), confirm_callback=lambda c: True)
r = await t.execute(command="echo hello")
assert "hello" in r.output
assert r.error is None
# 拒绝场景
t2 = RunTool(workspace=Path.cwd(), confirm_callback=lambda c: False)
r2 = await t2.execute(command="echo hello")
assert r2.error and "拒绝" in r2.error
```

---

## Phase 3: 协议与 Provider 改动

### T9: Delta 扩展 + Anthropic 工具调用解析

**文件：** `codepilot/protocols.py`
**依赖：** 无（纯增量改动，现有逻辑不受影响）
**步骤：**
1. 在 `protocols.py` 顶部新增 `ToolCall` 和 `ToolResult` 数据类
2. `Delta` 新增 `tool_calls: list[ToolCall] | None = None` 和 `tool_result: ToolResult | None = None`
3. `AnthropicProtocol.stream()` 新增逻辑：
   - 维护 `_tool_blocks: dict[int, dict]`（index → {id, name, args_json}）
   - 在 SSE 事件循环中识别 `content_block_start`（type=`tool_use`）→ 初始化 `_tool_blocks[idx]`
   - 识别 `content_block_delta`（type=`input_json_delta`）→ `_tool_blocks[idx]["args_json"] += delta["partial_json"]`
   - 识别 `content_block_stop` → 标记该 block 完成（暂不产出，等全部结束）
   - 在 `message_stop` 时：若 `_tool_blocks` 非空 → 解析每个 block 的 args_json 为 dict → 构建 `ToolCall` 列表 → `yield Delta(tool_calls=[...])`
   - 若有 tool_calls → 不再 yield `Delta(done=True)`（让 Agent 处理后续流程）
   - 若没有 tool_calls → 按现有逻辑 yield `Delta(done=True)`

**验证：** 构造 Anthropic SSE mock 事件流（含 tool_use block）→ 验证产出 `Delta(tool_calls=[...])`

### T10: OpenAI 工具调用解析

**文件：** `codepilot/protocols.py`
**依赖：** T9
**步骤：**
1. `OpenAIProtocol.stream()` 新增逻辑：
   - 维护 `_tool_calls: dict[int, dict]`（index → {id, name, args_json}）
   - 在 SSE 事件循环中识别 `choices[0].delta.tool_calls` 数组
   - 对每个 tool_call delta：
     - 若 `function.name` 有值 → `_tool_calls[idx]["name"] = ...`
     - 若 `function.arguments` 有值 → `_tool_calls[idx]["args_json"] += ...`
   - 识别 `finish_reason == "tool_calls"` → 解析每个 args_json → 构建 `ToolCall` 列表 → `yield Delta(tool_calls=[...])`
   - 若有 tool_calls → 不再 yield `Delta(done=True)`

**验证：** 构造 OpenAI SSE mock 事件流（含两个 tool_call 的交叉 delta）→ 验证产出两个正确的 `ToolCall`

### T11: Provider 层 tools 参数传递

**文件：** `codepilot/providers.py`
**依赖：** T9, T10
**步骤：**
1. `BaseProvider.stream()` 签名添加 `tools: list[dict] | None = None`
2. `AnthropicProvider.stream()`：
   - 将 `tools` 传入 `self._protocol.stream(messages, thinking=..., tools=tools)`
   - 在 AnthropicProtocol 中，若 tools 非空 → `body["tools"] = tools`
3. `OpenAIProvider.stream()`：
   - 同理，OpenAIProtocol 中若 tools 非空 → `body["tools"] = tools`

**验证：** mock provider.stream() 传入 tools 列表 → 检查发出的 HTTP body 中 tools 字段正确

---

## Phase 4: Agent 编排层

### T12: Agent 类

**文件：** `codepilot/agent.py`
**依赖：** T1-T11（所有 tools + protocols + providers 改动）
**步骤：**
1. 实现 `Agent.__init__`：接收 `provider`, `registry`, `workspace`, `timeout` 参数，识别 provider 协议类型（通过 `provider.config.protocol`）
2. 实现 `Agent._get_tools(protocol: str) → list[dict]`：调 `registry.to_anthropic_tools()` 或 `to_openai_tools()`
3. 实现 `Agent._inject_tool_results(messages, tool_calls, results, protocol)`：
   - Anthropic: 追加 `{"role": "user", "content": [{"type": "tool_result", "tool_use_id": tc.id, "content": r.output or r.error}]}`
   - OpenAI: 追加多条 `{"role": "tool", "tool_call_id": tc.id, "content": r.output or r.error}`
   - 同时追加 assistant 消息（含 tool_use content_blocks，此步 Anthropic 必需、OpenAI 可选）
4. 实现 `Agent.run(messages)`：
   ```
   tools = self._get_tools(protocol)
   async for delta in self.provider.stream(messages, tools=tools):
       if delta.tool_calls is None:
           yield delta  # 纯文本路径，透传
       else:
           break  # 停止第一轮，进入工具执行
   if delta.tool_calls is not None:
       # 并行执行
       tasks = []
       for tc in delta.tool_calls:
           tool = self.registry.get(tc.name)
           if tool is None:
               tasks.append(ToolResult(tc.id, tc.name, error=f"未知工具: {tc.name}"))
           else:
               tasks.append(asyncio.wait_for(tool.execute(**tc.arguments), timeout=tool.timeout))
       results = await asyncio.gather(*tasks, return_exceptions=True)
       # 异常转 ToolResult
       results = [ToolResult(tc.id, tc.name, error=str(r)) if isinstance(r, Exception) else r 
                  for tc, r in zip(delta.tool_calls, results)]
       # 逐个产出 tool_result
       for r in results:
           yield Delta(tool_result=r)
       # 注入历史
       self._inject_tool_results(messages, delta.tool_calls, results, protocol)
       # 第二轮请求
       async for delta2 in self.provider.stream(messages, tools=tools):
           if delta2.text or delta2.done or delta2.error:
               yield delta2
   ```

**验证：** mock Provider → Agent.run() 完整流程单元测试

---

## Phase 5: Chat 与 TUI 集成

### T13: ChatManager 委托 Agent

**文件：** `codepilot/chat.py`
**依赖：** T12
**步骤：**
1. `ChatManager.__init__` 参数 `provider: BaseProvider` 改为 `agent: Agent`
2. `send_message()` 中 `self.provider.stream(ctx)` 改为 `self.agent.run(self.build_context())`
3. tool_result delta 透传（不在 history 中记录，Agent 内部已处理）
4. 确保非工具对话的 text 累积 + history 写入逻辑不变
5. 更新 `__init__.py` 的导入（如有）

**验证：** 发送纯文本消息 → 确认行为与 MVP 一致

### T14: TUI 工具行展示

**文件：** `codepilot/app.py`
**依赖：** T13
**步骤：**
1. 在 `on_input_submitted()` 中新增 Delta 类型处理：
   - `delta.tool_calls` 到达时：
     - 为每个 ToolCall 创建 `Static` widget，id 为 `f"tool-{tc.id}"`
     - 初始文本：`● {tc.name}({简化的参数})`，如 `● read(app.py)`
     - 样式：dim + 斜体，与对话消息视觉区分
     - 挂载到 RichLog 下方
   - `delta.tool_result` 到达时：
     - 查找对应 id 的 Static widget
     - 成功：更新为 `● {name}({摘要})`，如 `● Read(42 lines)`
     - 失败：更新为 `✕ Error: {error[:80]}`
     - 完成后移除临时状态标记
2. 确保 `delta.text` 的流式展示逻辑不变

**验证：** 启动应用 → 发送需工具调用的消息 → 观察工具行的出现和更新

### T15: 命令确认列表选择

**文件：** `codepilot/app.py`
**依赖：** T14
**步骤：**
1. 创建 `ConfirmList` 组件（Claude Code 风格）：
   - 显示完整待执行命令
   - 三选项列表：1. 批准执行 / 2. 拒绝执行 / 3. 始终允许（本次会话）
   - ↑↓ 键在选项间移动高亮光标，Enter 确认当前高亮项
   - 数字键 1/2/3 直接选择对应选项
   - 使用 `asyncio.Event` 等待用户选择（不碰屏幕栈，避免 Enter 泄漏）
2. `CodePilotApp` 新增 `_confirm_command(command: str) → bool` 方法：
   - 挂载 `ConfirmList` 到当前屏幕 → await 用户选择 → 移除组件
   - 选"始终允许"时设置会话级标志，后续 `run` 自动跳过确认
   - 返回 bool
3. RunTool 的 `confirm_callback` 在 App.on_mount 中注入

**验证：** 发送触发 run 工具的消息 → 列表出现 → ↑↓ 切换 → Enter 选择 → 批准/拒绝/始终允许 三路径各测一次

### T16: main.py 组装

**文件：** `codepilot/main.py`
**依赖：** T15
**步骤：**
1. 导入 `Agent`, `ToolRegistry`, 六个工具类
2. 在 `main()` 中（Provider 创建之后、App 创建之前）：
   ```
   registry = ToolRegistry()
   registry.register(ReadTool(workspace=Path.cwd()))
   registry.register(WriteTool(workspace=Path.cwd()))
   registry.register(EditTool(workspace=Path.cwd()))
   registry.register(GlobTool(workspace=Path.cwd()))
   registry.register(GrepTool(workspace=Path.cwd()))
   registry.register(RunTool(workspace=Path.cwd(), confirm_callback=None))  # callback 在 App 中注入
   agent = Agent(provider=provider, registry=registry, workspace=Path.cwd())
   app = CodePilotApp(cfg, agent)
   ```
3. `CodePilotApp.__init__` 接收 `agent` 而不是 `config`，内部创建 `ChatManager(agent)`
4. `RunTool` 的 `confirm_callback` 在 App.on_mount() 中注入（解决循环依赖）

**验证：** `python -m codepilot` 正常启动，纯文本对话功能正常

---

## Phase 6: 测试

### T17: 测试基础设施 + tools/base 测试

**文件：** `tests/__init__.py`, `tests/conftest.py`, `tests/test_tools_base.py`
**依赖：** T1, T2
**步骤：**
1. 创建 `tests/` 目录 + `__init__.py`
2. 创建 `conftest.py`：提供 `tmp_workspace` fixture（创建临时目录 + 测试文件）
3. `test_tools_base.py`：
   - `test_validate_path_within_workspace`：正常路径通过
   - `test_validate_path_traversal`：`../` 穿越被拒绝
   - `test_validate_path_absolute`：绝对路径被拒绝
   - `test_tool_abc_enforces_interface`：不实现 abstract 方法 → TypeError

**验证：** `python -m pytest tests/test_tools_base.py -v`

### T18: Registry 测试

**文件：** `tests/test_tools_registry.py`
**依赖：** T17, T2
**步骤：**
- `test_register_and_get`
- `test_list_all`
- `test_duplicate_name_overwrites`
- `test_to_anthropic_tools`：验证输出格式含 name/description/input_schema
- `test_to_openai_tools`：验证输出格式含 type/function/name/description/parameters

**验证：** `python -m pytest tests/test_tools_registry.py -v`

### T19-T23: 六个工具测试

**文件：** `tests/test_tools_read.py` 到 `tests/test_tools_run.py`
**依赖：** T17, T3-T8
**每个工具至少覆盖：**
- 正常执行成功
- 文件不存在/路径越界 → error 返回
- 超时行为（mock）
- 截断行为（超大数据）
- RunTool 特有：批准/拒绝/超时 kill

**验证：** `python -m pytest tests/test_tools_*.py -v`

### T24: 协议 + Provider 测试更新

**文件：** `tests/test_protocols.py`, `tests/test_providers.py`
**依赖：** T9, T10, T11
**步骤：**
- `test_protocols.py` 新增：
  - `test_anthropic_tool_use_single`：mock 单工具调用 SSE
  - `test_anthropic_tool_use_multiple`：mock 多工具调用 SSE
  - `test_anthropic_tool_use_with_thinking`：thinking 增量不混入 tool_call
  - `test_openai_tool_calls_single`：mock 单 tool_call SSE
  - `test_openai_tool_calls_multiple_interleaved`：mock 交叉 delta 的两个 tool_call
  - `test_openai_text_and_tool_calls`：文本 + 工具调用混合
- `test_providers.py` 新增：
  - `test_stream_sends_tools_anthropic`：验证 tools 注入 body
  - `test_stream_sends_tools_openai`：验证 tools 注入 body

**验证：** `python -m pytest tests/test_protocols.py tests/test_providers.py -v`

### T25: Agent 测试 + 端到端

**文件：** `tests/test_agent.py`
**依赖：** T12, T24
**步骤：**
- `test_agent_pure_text`：无工具调用 → text delta 正常透传
- `test_agent_single_tool`：mock 单工具调用 → 工具执行 → 第二轮返回
- `test_agent_parallel_tools`：mock 三个工具调用 → 并发执行（验证总耗时）
- `test_agent_tool_error`：工具返回 error → 注入 history → 第二轮正常
- `test_agent_unknown_tool`：模型调用不存在的工具 → error ToolResult
- `test_agent_roundtrip_history`：验证 messages 中正确包含 tool_use + tool_result

**验证：** `python -m pytest tests/test_agent.py -v`  
**端到端：** `python -m pytest tests/ -v`（全部测试通过）

---

## 执行顺序

```
Phase 1: 基础设施
T1 (base + ABC) → T2 (Registry)
                        │
Phase 2: 六个工具（可并行）│
T3 (Read)  T4 (Write)  T5 (Edit)  T6 (Glob)  T7 (Grep)  T8 (Run)
    │           │           │           │           │           │
    └───────────┴───────────┴───────────┴───────────┴───────────┘
                              │
Phase 3: 协议与 Provider       │
T9 (Delta + Anthropic) → T10 (OpenAI) → T11 (Provider)
                              │
Phase 4: Agent                 │
T12 (Agent) ←─────────────────┘
    │
Phase 5: 集成
T13 (Chat) → T14 (TUI 工具行) → T15 (确认列表) → T16 (main 组装)
    │
Phase 6: 测试（可与实现并行）
T17 (base test) → T18 (registry test) → T19-T23 (tools test) → T24 (protocol test) → T25 (agent test)
```

---

## 任务统计

| Phase | 任务数 | 文件 |
|-------|--------|------|
| Phase 1: 基础设施 | 2 | tools base + registry |
| Phase 2: 六个工具 | 6 | read/write/edit/glob/grep/run |
| Phase 3: 协议/Provider | 3 | protocols × 2 + providers × 1 |
| Phase 4: Agent | 1 | agent.py |
| Phase 5: Chat/TUI/集成 | 4 | chat + app × 2 + main |
| Phase 6: 测试 | 9 | 全量测试 |
| **合计** | **25** | |

每个 Phase 完成后提交一次 Commit。
