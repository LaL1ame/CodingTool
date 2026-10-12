# CodePilot 多行输入（TextArea 改造）Plan

## 目标
TUI 输入框从单行 `Input` 换成多行 `TextArea`，支持粘贴/编辑多行提示词。

## 关键决策（最终确认，经翻转）
- **提交键 = Enter 发送，Ctrl+Enter 换行**（保留回车即发，多行用 Ctrl+Enter）。
- 翻转原因：真机实测 Ctrl+Enter 在终端不可靠（TextArea 内部吞掉——既不发送也不换行）。

## 关键技术发现：声明式 BINDINGS 拦不住 Enter
`TextArea._on_key` 内部把 `enter`→`"\n"`，绕过 BINDINGS。需**子类 `PromptInput` 覆写 `_on_key`**：
- enter → post `Submitted` 消息；ctrl+enter → 镜像基类 `_replace_via_keyboard("\n")` 换行；其余 → `super()`。

## 技术要点（Textual 8.2.8 实测）
- `TextArea.text` 读写（非 `.value`）；`.clear()`、`.disabled`。
- `TextArea` 无 placeholder、无内置提交消息 → 自定义 `Submitted(Message)`。

## 改动清单
1. 新增 `codepilot/ui/widgets/prompt_input.py`：`PromptInput(TextArea)` + `Submitted(Message)` + 覆写 `_on_key`。
2. `codepilot/ui/app.py`：compose yield `PromptInput(id="prompt")`；`on_prompt_input_submitted`（读→clear→`_submit`）；移除 App 级 ctrl+enter 绑定；`_submit(text)` 单一发送入口。
3. `codepilot/ui/theme.tcss`：`#prompt` 定高 3 + `#prompt-hint` 提示行。
4. docs 三份。
3. docs 三份（本 plan + task + checklist）。

## 验证
- 无头 `run_test` 挂载：TextArea 挂载/多行编辑/ctrl+enter 绑定/clear/纯命令路由。
- `pytest -q` 264 无回归。
- 真机：粘多行 prompt → ctrl+enter 完整进入对话；`/continue`/`/compact`/`/exit` 单行命令可用。

## 不做（YAGNI/边界）
- 不换 submit 语义为 Enter（已定 B）。
- 不引入输入高亮/lint（草案类，非本 state 目标）。
- 命令 `/exit` 仍回车发送改为 ctrl+enter（多行语义统一）。