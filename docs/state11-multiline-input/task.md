# CodePilot 多行输入（TextArea 改造）Task

## T1: 替换控件与绑定
- [x] `app.py` import `Input` → `TextArea`。
- [x] `BINDINGS` 增 `("ctrl+enter","submit","发送")`。

## T2: compose 布局
- [x] 加 `Static(id="prompt-hint")` 提示行（`Ctrl+Enter 发送 · Enter 换行 · 支持多行提示词`）。
- [x] `Input(id="prompt")` → `TextArea(id="prompt")`。
- [x] `theme.tcss`：`#prompt` 定高 3 + `#prompt-hint` 样式。

## T3: 提交入口重构
- [x] `on_input_submitted` → `action_submit`（读 `.text`、strip、清空、调 `_submit`；空/流式守卫）+ `_submit(text)`（命令解析 + 纯命令/对话分发，单一入口）。

## T4: 清理残留 Input/`.value`
- [x] 4 处 `query_one("#prompt", Input).value=""` → `TextArea.clear()`。
- [x] `_process_chat` 内 `Input` → `TextArea` 类型标注。

## T5: 验证
- [x] 无头 `run_test` 挂载冒烟：mount/多行编辑/ctrl+enter 绑定/clear/纯命令路由。
- [x] `pytest -q` 264 无回归。
- [ ] 真机人工：粘多行 prompt → ctrl+enter 完整进入对话；单行 `/continue` 等可用。

## 完成判定
- 编译/导入通过；264 测试绿；挂载冒烟通过；真机多行输入生效。