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

## 关键决策（最终确认，历经一次翻转）
初始倾向 B（Ctrl+Enter 发送、Enter 换行），真机实测发现 **Ctrl+Enter 在终端不可靠（TextArea 内部把 ctrl+enter 当非可打印键吞掉，既不发送也不换行）**。经用户确认翻转：

- **Enter = 发送**（保留回车即发的肌肉记忆，单行 /exit /continue 零学习成本）。
- **Ctrl+Enter = 换行**（长文本/多行需要时用）。

## 关键技术发现：声明式 BINDINGS 拦不住 Enter
`TextArea` 在内部 `_on_key`（`_text_area.py:1818`）把 `"enter" → "\n"` 直接映射为插换行，**绕过声明式 BINDINGS**。实测在子类加 `("enter", ...)` 绑定不生效（enter 仍插换行）。故用**子类 `PromptInput` 覆写 `_on_key`**：
- `event.key == "enter"` → `event.stop()` + post `Submitted` 消息（App 收取后分发）。
- `event.key == "ctrl+enter"` → `event.stop()` + 镜像基类对 enter 的 `_replace_via_keyboard("\n", …)` 实现换行。
- 其余按键 → `await super()._on_key(event)`（打字/退格/方向等保真）。

## 实现要点（最终）
1. 新增 `codepilot/ui/widgets/prompt_input.py`：`PromptInput(TextArea)`，`Submitted(Message)`，覆写 `_on_key` 如上。
2. `app.py`：`compose` yield `PromptInput(id="prompt")`；新增 `on_prompt_input_submitted`（读 `.text`→`clear()`→统一 `_submit`）；移除 App 级 ctrl+enter 绑定；`on_input_submitted` 逻辑抽为 `_submit(text)` 单一发送入口。
3. 提示行 `#prompt-hint` 文案：`Enter 发送 · Ctrl+Enter 换行 · 支持多行提示词`。
4. CSS `#prompt { height: 3 }` 定高、超长内部滚动。
5. 所有 `query_one("#prompt", Input).value=""` → `TextArea.clear()`；`.disabled` 不变。

## 影响文件
- `codepilot/ui/app.py`（compose / 绑定 / action_submit / _dispatch / 各 .value 重置点）
- `codepilot/ui/theme.tcss`（#prompt 高度/样式）
- `tests/`（无 TUI 单测；以 import + 冒烟验证）
- 更新 `docs/state11-multiline-input/{plan,task,checklist}.md`

## 验证
- import / compileall 冒烟。
- 真机：粘多行 prompt → 提交键 → 完整进入对话；`/continue`/`/compact` 单行命令可用。
- 现有 264 测试全绿（无回归，UI 改动不涉 agent 层）。