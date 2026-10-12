# CodePilot 多行输入（TextArea 改造）Task

## T1: 新增 PromptInput 组件
- [x] 新增 `codepilot/ui/widgets/prompt_input.py`：`PromptInput(TextArea)` + `Submitted(Message)`。
- [x] 覆写 `_on_key`：`enter`→post Submitted 发送；`ctrl+enter`→`_replace_via_keyboard("\n")` 换行；其余→`super()`。（原因：TextArea 内部 `_on_key` 把 enter 直映射换行，BINDINGS 拦不住。）

## T2: compose 布局
- [x] 加 `Static(id="prompt-hint")` 提示行（`Enter 发送 · Ctrl+Enter 换行 · 支持多行提示词`）。
- [x] `Input(id="prompt")` → `PromptInput(id="prompt")`。
- [x] `theme.tcss`：`#prompt` 定高 3 + `#prompt-hint` 样式。

## T3: 提交入口重构
- [x] `on_prompt_input_submitted`（收 Submitted：读 `.text`、strip、清空、调 `_submit`；空/流式守卫）。
- [x] `_submit(text)`：命令解析 + 纯命令/对话分发，单一发送入口。
- [x] 移除 App 级 `ctrl+enter` 绑定（Enter 由 PromptInput 内部接管）。

## T4: 清理残留 Input/`.value`
- [x] 4 处 `query_one("#prompt", Input).value=""` → `TextArea.clear()`。
- [x] `_process_chat` 内类型标注 `textual Input` → `TextArea`。

## T5: 验证
- [x] `PromptInput` 组件行为：打字→编辑；enter→提交不插换行；ctrl+enter→换行；多行内容完整发送（run_test 实测）。
- [x] `pytest -q` 264 无回归。
- [ ] 真机人工：Enter 发送、Ctrl+Enter 换行、粘多行完整进入对话。

## 完成判定
- 编译/导入通过；264 测试绿；组件行为实测通过；真机人工确认。