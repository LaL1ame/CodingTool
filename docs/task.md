# MewCode Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `pyproject.toml` | 项目元数据 + 依赖声明 |
| 新建 | `mewcode/__init__.py` | 包初始化 |
| 新建 | `mewcode/config.py` | YAML 读取、校验、ProviderConfig |
| 新建 | `mewcode/protocols/__init__.py` | 包初始化 |
| 新建 | `mewcode/protocols/base.py` | BaseProtocol + Delta |
| 新建 | `mewcode/protocols/anthropic.py` | AnthropicProtocol (SSE + thinking 过滤) |
| 新建 | `mewcode/protocols/openai.py` | OpenAIProtocol (SSE) |
| 新建 | `mewcode/providers/__init__.py` | 包初始化 |
| 新建 | `mewcode/providers/base.py` | BaseProvider + 工厂方法 |
| 新建 | `mewcode/providers/anthropic.py` | AnthropicProvider |
| 新建 | `mewcode/providers/openai.py` | OpenAIProvider |
| 新建 | `mewcode/chat/__init__.py` | 包初始化 |
| 新建 | `mewcode/chat/models.py` | Message 数据类 |
| 新建 | `mewcode/chat/manager.py` | ChatManager |
| 新建 | `mewcode/tui/__init__.py` | 包初始化 |
| 新建 | `mewcode/tui/app.py` | MewCodeApp + 计时器 |
| 新建 | `mewcode/tui/widgets/__init__.py` | 包初始化 |
| 新建 | `mewcode/tui/widgets/banner.py` | ASCII 猫咪横幅 |
| 新建 | `mewcode/tui/widgets/chat_area.py` | 对话区 + Markdown 渲染 |
| 新建 | `mewcode/tui/widgets/input_box.py` | 多行输入 + 锁定 |
| 新建 | `mewcode/tui/widgets/provider_select.py` | Provider 选择屏幕 |
| 新建 | `mewcode/tui/widgets/status_bar.py` | 状态栏 |
| 新建 | `mewcode/main.py` | 入口：编排启动流程 |
| 新建 | `config.example.yaml` | 示例配置 |
| 新建 | `tests/test_config.py` | 配置层测试 |
| 新建 | `tests/test_protocols.py` | 协议层测试 |
| 新建 | `tests/test_providers.py` | Provider 层测试 |
| 新建 | `tests/test_chat.py` | 对话管理层测试 |

---

## T1: 项目骨架 + pyproject.toml ✅
**文件：** `pyproject.toml`, `mewcode/__init__.py`
**依赖：** 无
**验证：** `python -c "import mewcode"` 无错误

## T2: 配置层 — ProviderConfig + YAML 加载 ✅
**文件：** `mewcode/config.py`
**依赖：** T1
**验证：** 合法/非法 YAML 各场景通过 `load_config()`

## T3: 协议层基类 — Delta + BaseProtocol ✅
**文件：** `mewcode/protocols/base.py`
**依赖：** T1
**验证：** 编译通过，无法直接实例化 BaseProtocol

## T4: Anthropic 协议实现 ✅
**文件：** `mewcode/protocols/anthropic.py`
**依赖：** T3
**验证：** mock httpx 模拟 SSE 事件流

## T5: OpenAI 协议实现 ✅
**文件：** `mewcode/protocols/openai.py`
**依赖：** T3
**验证：** mock httpx 模拟 SSE 事件流

## T6: Provider 抽象层 ✅
**文件：** `mewcode/providers/base.py`, `anthropic.py`, `openai.py`
**依赖：** T2, T3, T4, T5
**验证：** 工厂方法创建正确实例，未知协议抛 ValueError

## T7: 对话管理 — Message + ChatManager ✅
**文件：** `mewcode/chat/models.py`, `manager.py`
**依赖：** T6
**验证：** mock Provider 验证上下文组包和消息生命周期

## T8: TUI 基础框架 + 横幅 + 状态栏 ✅
**文件：** `mewcode/tui/app.py`, `widgets/banner.py`, `widgets/status_bar.py`
**依赖：** T7
**验证：** `python -m mewcode` 能启动并看到横幅、输入框、状态栏

## T9: TUI 对话区 + Markdown 渲染 ✅
**文件：** `mewcode/tui/widgets/chat_area.py`
**依赖：** T8
**验证：** 模拟消息流式更新和 Markdown 渲染

## T10: TUI 输入框 + 流式锁定 ✅
**文件：** `mewcode/tui/widgets/input_box.py`
**依赖：** T8
**验证：** Enter 提交、Alt+Enter 换行、流式期间锁定

## T11: TUI 完整事件串联 ✅
**文件：** `mewcode/tui/app.py` (更新)
**依赖：** T9, T10
**验证：** 完整启动 → 输入 → 流式 → Markdown → 多轮上下文

## T12: 入口 + 多 Provider 选择 ✅
**文件：** `mewcode/main.py`, `config.example.yaml`
**依赖：** T2, T6, T7, T11
**验证：** 单/多 provider 两种场景完整启动

## T13: 集成测试 + 端到端验证 ✅
**文件：** `tests/` (测试文件)
**依赖：** T12
**验证：** `python -m pytest tests/ -v` — 31 tests passed

---

## 执行顺序

```
T1
 ├──→ T2 ──────────────────────────┐
 │                                  │
 ├──→ T3 ──→ T4 ──→ T5 ──→ T6 ──→ T7 ──→ T8 ──→ T9 ──→ T11 ──→ T12 ──→ T13
 │                                       │                      │
 │                                       └──→ T10 ─────────────┘
 │
 └── (T4/T5 可并行、T9/T10 可并行、T2/T3 可并行)
```
