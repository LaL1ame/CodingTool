# CodePilot 多行输入（TextArea 改造）Plan

## 目标
TUI 输入框从单行 `Input` 换成多行 `TextArea`，支持粘贴/编辑多行提示词。

## 关键决策（已确认）
- **提交键 = B：Ctrl+Enter 发送，Enter 换行**（文本编辑器/Claude Code 惯例）。
- `TextArea` 无 placeholder、无内置提交 → 提示语上移到独立行；自加 `ctrl+enter` → `action_submit`。

## 技术要点（Textual 8.2.8 实测）
- `TextArea.text` 读写（非 `.value`）；`.clear()`、`.disabled`。
- `TextArea.BINDINGS` 无 `enter` → Enter 默认插换行，无需覆盖。
- App 级 `BINDINGS` 加 `("ctrl+enter","submit","发送")`，TextArea 不拦截 ctrl+enter → 生效。

## 改动清单
1. `codepilot/ui/app.py`
   - import: `Input` → `TextArea`。
   - `BINDINGS` 加 ctrl+enter。
   - `compose`: 加 `Static(id="prompt-hint")` 提示行 + `TextArea(id="prompt")` 替代 `Input`。
   - `on_input_submitted` → 拆为 `action_submit`（读入+清空+分发）与 `_submit(text)`（命令解析/对话分发，单一入口）。
   - 删 4 处 `query_one("#prompt", Input).value=""` → `TextArea.clear()`；`_process_chat` 类型改 `TextArea`。
2. `codepilot/ui/theme.tcss`：`#prompt` 定高 3（内部滚动）+ 新增 `#prompt-hint` 样式。
3. docs 三份（本 plan + task + checklist）。

## 验证
- 无头 `run_test` 挂载：TextArea 挂载/多行编辑/ctrl+enter 绑定/clear/纯命令路由。
- `pytest -q` 264 无回归。
- 真机：粘多行 prompt → ctrl+enter 完整进入对话；`/continue`/`/compact`/`/exit` 单行命令可用。

## 不做（YAGNI/边界）
- 不换 submit 语义为 Enter（已定 B）。
- 不引入输入高亮/lint（草案类，非本 state 目标）。
- 命令 `/exit` 仍回车发送改为 ctrl+enter（多行语义统一）。