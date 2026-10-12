# CodePilot 多行输入（TextArea 改造）Spec

## 目标
把 TUI 输入从单行 `Input` 换成多行 `TextArea`，让用户可以粘贴/编辑多行提示词，不被截断成首行。

## 现状问题
- `codepilot/ui/app.py` compose 用 `Input(id="prompt")`（Textual 单行控件）。
- 多行粘贴只保留第一行；验证多轮任务的长 prompt 无法一次输入。

## 技术约束（Textual 8.2.8 实测）
- `textual.widgets.TextArea` 存在，支持多行编辑/滚动/Emacs 键位/撤销。
- `TextArea` 读写用 **`.text`**（非 `.value`），`.clear()` 清空，`.disabled` 可用。
- `TextArea` **无 placeholder 参数**、**无内置 submit 事件→需要自定义**。
- `TextArea.BINDINGS` **无 enter** → Enter 默认插换行（正合多行编辑）。

## 需要用户拍板的决策：提交键
TextArea Enter=换行后，必须有明确的分隔「换行 / 发送」键。两个方案（互斥）：

- **A｜Enter=发送，Alt+Enter=换行**
  保持现有肌肉记忆（回车即发）；长文本需 Alt+Enter 换行。
  - 优点：单行快捷指令（/exit /compact /continue）回车即发，零学习成本。
  - 缺点：写多行要记 Alt+Enter，粘贴的多行不进编辑缓冲（粘贴即提交）。

- **B｜Ctrl+Enter=发送，Enter=换行（推荐）**
  文本编辑器/Claude Code 常规多行输入语义：Enter 编辑，Ctrl+Enter 发送。
  - 优点：多行编辑自然，粘贴任意多行→Ctrl+Enter 提交。
  - 缺点：单行指令也从 Enter 变 Ctrl+Enter，需改习惯。

## 实现要点（选型后）
1. `compose()`: `Input` → `TextArea(id="prompt")`；CSS 固定高度（如 `height: 3`，超长内部滚动）。
2. placeholder 提示移到输入区上方一条状态行：`Ctrl+Enter 发送 · Enter 换行 · /exit /plan /do`。
3. 新增类级绑定 `Binding(提交键, "submit", "发送")` + `action_submit`：读 `.text` → 清空 → 走统一 `_dispatch(text)`。
4. 把现有 `on_input_submitted` 逻辑抽成 `_dispatch(text)`，供 action_submit 复用（单一发送入口）。
5. 所有 `self.query_one("#prompt", Input).value = ""` → 换成 TextArea `.clear()` / `.text`；`.disabled` 用法不变。
6. `_process_chat` 中 `Input` 类型标注统一改 `TextArea`。

## 影响文件
- `codepilot/ui/app.py`（compose / 绑定 / action_submit / _dispatch / 各 .value 重置点）
- `codepilot/ui/theme.tcss`（#prompt 高度/样式）
- `tests/`（无 TUI 单测；以 import + 冒烟验证）
- 更新 `docs/state11-multiline-input/{plan,task,checklist}.md`

## 验证
- import / compileall 冒烟。
- 真机：粘多行 prompt → 提交键 → 完整进入对话；`/continue`/`/compact` 单行命令可用。
- 现有 264 测试全绿（无回归，UI 改动不涉 agent 层）。