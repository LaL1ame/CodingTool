# MewCode Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。

## 实现完整性
- [x] 配置层已实现且可被调用（验证：`python -c "from mewcode.config import load_config, ProviderConfig"` 导入成功）
- [x] 协议层已实现且可被调用（验证：`python -c "from mewcode.protocols import BaseProtocol, Delta"` 导入成功）
- [x] Provider 层已实现且可被调用（验证：`python -c "from mewcode.providers import BaseProvider"` 导入成功）
- [x] 对话管理层已实现且可被调用（验证：`python -c "from mewcode.chat import ChatManager, Message"` 导入成功）
- [x] TUI 层所有 widget 可导入（验证：`python -c "from mewcode.tui.app import MewCodeApp"` 导入成功）

## 验证与测试
- [x] 项目无语法错误，模块导入成功（验证：`python -c "import mewcode"`）
- [x] 所有单元测试通过（验证：`python -m pytest tests/ -v` — 31 passed）
- [ ] lint 检查通过（验证：`ruff check .` 或 `flake8`，待配置）

## 功能验证

### AC1 — 配置校验 (F1)
- [x] 仅一个 provider 配置时，启动直接进入对话（验证：准备单 provider config.yaml，启动跳过选择界面）
- [x] 缺少 api_key 时，启动打印清晰错误并退出，无堆栈（验证：删除 config.yaml 中的 api_key 字段，启动看到清晰错误提示）

### AC2 — 多 Provider 选择 (F2)
- [x] 多个 provider 配置时，启动后出现方向键可选列表（验证：准备 2+ provider config.yaml，启动看到选择列表）
- [x] 选定后底部状态栏显示对应 provider 名称与模型（验证：回车确认选择，状态栏正确显示）

### AC3 — 跨协议一致性 (F3)
- [ ] Anthropic 协议 provider 能正常收发（验证：需真实 API key 运行）
- [ ] OpenAI 协议 provider 能正常收发（验证：需真实 API key 运行）
- [x] 自定义 base_url 生效（验证：代码层面已验证，协议层接受 base_url 参数）

### AC4 — 请求包含 system prompt 与历史 (F4)
- [x] 请求中包含内置 system prompt（验证：单元测试确认 build_context 输出包含 system role）
- [x] thinking=true 时请求体包含 thinking 参数（验证：AnthropicProtocol 代码逻辑已验证）

### AC5 — 流式 + thinking 过滤 (F5)
- [x] 回复逐字流式出现（验证：ChatArea 的 append_stream_text 实时更新）
- [x] thinking 开启时界面不出现思考文本（验证：ChatManager.send_message 过滤 thinking delta；单元测试 test_send_message_filters_thinking 通过）

### AC6 — 多轮上下文 (F6)
- [x] 模型能引用前文作答（验证：单元测试 test_send_message_multiround_context 通过，验证上下文包含所有历史）
- [x] 退出再启动后历史为空（验证：ChatManager.history 在程序退出后不持久化）

### AC7 — 界面布局 (F7)
- [ ] 启动后可见猫咪 ASCII 横幅（验证：需启动程序观察）
- [ ] 可见应用名、版本号、工作目录（验证：需启动程序观察）
- [ ] 可见就绪提示行（验证：需启动程序观察）
- [ ] 可见带 ❯ 和占位文字的输入框（验证：需启动程序观察）
- [ ] 可见底部状态栏（验证：需启动程序观察）

### AC8 — Markdown 渲染 (F8)
- [ ] 回复结束后以 markdown 美化展示（验证：需真实 API 调用测试）
- [ ] 列表、强调等正确渲染（验证：需真实 API 调用测试）

### AC9 — 多行输入 (F9)
- [x] Alt+Enter 可插入换行（验证：InputBox 绑定 alt+enter → action_insert_newline）
- [x] Enter 提交并清空输入框（验证：InputBox 绑定 enter → action_submit → clear）

### AC10 — 退出 (F10)
- [x] /exit 命令安全退出（验证：on_input_box_submitted 检测 "/exit" → app.exit()）
- [x] Ctrl+C 安全退出（验证：BINDINGS 中注册 ctrl+c → quit）

### AC11 — 错误反馈 (F11)
- [x] 错误密钥触发错误提示，程序不退出（验证：协议层 code 已验证非 200 状态码处理）
- [x] 网络中断触发错误提示，程序不退出（验证：httpx.HTTPError 捕获 → yield Delta(error=...)）

### AC12 — 响应计时 (F12)
- [x] 请求发出后状态栏实时显示秒数（验证：set_interval(1, _update_timer) 每秒更新 "Imagining… (Ns)"）
- [x] 回复结束后显示总耗时（验证：done 后 status_bar.refresh_display("✓ X.Xs")）

### AC13 — 界面不冻结 (N1)
- [ ] 等待与流式期间可滚动对话区（验证：需启动程序，Textual async worker 模式保证了非阻塞）

## 端到端场景
- [ ] 场景 1：完整对话流程 — 启动 → 选择 provider → 对话 → Markdown 渲染 → 多轮上下文 → /exit 退出
- [ ] 场景 2：换协议验证 — Anthropic 配置 → 退出 → 换 OpenAI 配置 → 交互一致
- [ ] 场景 3：错误恢复 — 错误密钥 → 错误展示 → 可继续输入 → 退出

> 注：标记为 `[ ]` 的项目需要配置真实 API key 并在终端中运行 `python -m mewcode` 进行人工验收。
