# CodePilot 多行输入（TextArea 改造）Checklist

## 实现
- [x] 新增 `PromptInput(TextArea)` 组件（widgets/prompt_input.py），非单行 `Input`。
- [x] 提示行 `#prompt-hint` 展示提交/换行说明。
- [x] 无 App 级 ctrl+enter 绑定（Enter 由 PromptInput 内部接管）。

## 行为（AC）
- [x] **Enter 发送**：`_on_key` 拦截 enter → post `Submitted` → `on_prompt_input_submitted` 读 `.text`/clear/`_submit`（run_test 实测：enter 后不插换行、内容发出）。
- [x] **Ctrl+Enter 换行**：`_on_key` 拦截 ctrl+enter → `_replace_via_keyboard("\n")`（run_test 实测：`a`+ctrl+enter+`b` → `a\nb`）。
- [x] 多行编辑：粘贴/打字 >1 行不截断，多行内容 Enter 完整发送（实测 `a\nb` 全量发出）。
- [x] 纯命令路由：`/mode` `/exit` `/plan` `/do` `/compact` `/continue` `/edits` 走 `_submit`。
- [x] 流式/禁用状态：`_submit` 置 `inp.disabled=True`，`_process_chat` finally 恢复。
- [x] 输入区定高 3、超长内部滚动（theme.tcss `#prompt { height: 3 }`）。

## 回归
- [x] 无残留 `Input(id="prompt")` / widget `.value` 引用（grep）。
- [x] import/compileall 冒烟 OK。
- [x] `pytest -q` → 264 passed（无回归）。

## 待人工（真机）
- [ ] Enter 发送、Ctrl+Enter 换行手感。
- [ ] 粘多行 prompt → 完整进入对话。
- [ ] 输入框 3 行渲染、超长内部滚动正常。

## 备注
- **决策经翻转**：先 B（Ctrl+Enter 发送）→ 真机实测 Ctrl+Enter 在终端不可靠（TextArea 内部吞掉）→ 翻转确认 **Enter 发送、Ctrl+Enter 换行**。
- 关键技术点：`TextArea._on_key` 内部把 enter 直映射换行，声明式 BINDINGS 拦不住 → 用子类覆写 `_on_key`。
- 此 state 仅 UI 层，不动 agent/context 逻辑；264 单测全部无关且全绿。