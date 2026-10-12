# CodePilot 多行输入（TextArea 改造）Checklist

## 实现
- [x] 新增 `PromptInput(TextArea)` 组件（widgets/prompt_input.py），非单行 `Input`。
- [x] 提示行 `#prompt-hint` 展示提交/换行说明。
- [x] 无 App 级 ctrl+enter 绑定（Enter 由 PromptInput 内部接管）。

## 行为（AC）
- [x] **Enter 发送**：`_on_key` 拦截 enter → post `Submitted` → `on_prompt_input_submitted` 读 `.text`/clear/`_submit`（run_test 实测：enter 后不插换行、内容发出；真机确认）。
- [x] **Ctrl+J 换行**：`_on_key` 拦截 ctrl+j → `_replace_via_keyboard("\n")`（run_test 实测：`a`+ctrl+j+`b` → `a\nb`；真机确认——唯 ctrl+j 在本机终端独立送达）。
- [x] 多行编辑：粘贴/打字 >1 行不截断，多行内容 Enter 完整发送（实测 `a\nb` 全量发出）。
- [x] 纯命令路由：`/mode` `/exit` `/plan` `/do` `/compact` `/continue` `/edits` 走 `_submit`。
- [x] 流式/禁用状态：`_submit` 置 `inp.disabled=True`，`_process_chat` finally 恢复。
- [x] **自动长高**：`#prompt { height: auto; max-height: 40% }` + `soft_wrap=True`，随内容增长、达 40% 屏高转内部滚动（实测 line 5→h=5）。
- [x] **启动自动聚焦**：`on_mount` 里 `query_one("#prompt").focus()`，否则 focus 落在 RichLog、按键不达 prompt（实测修复根因）。

## 回归
- [x] 无残留 `Input(id="prompt")` / widget `.value` 引用（grep）。
- [x] import/compileall 冒烟 OK。
- [x] `pytest -q` → 264 passed（无回归）。

## 验收（真机，已完成）
- [x] Enter 发送、Ctrl+J 换行——用户真机确认。
- [x] 输入框随内容自动长高、超长内部滚动——确认观感改善、无需上下键翻。
- [x] 粘贴多行 → Enter 完整发送（paste 自带换行，不依赖换行键）。

## 备注
- **换行键最终定为 Ctrl+J**：真机逐步排查——Enter 发送成立；Ctrl/Shift/Alt+Enter 在本机 Windows 终端全被折叠成 enter(等于发送)，被判不可用；唯有 `ctrl+j`（LF 控制符）独立送达、可靠换行。已按实机结论把多余换行键从 `_NEWLINE_KEYS` 移除，只留 ctrl+j。
- **跳过 Enter 但能多行的另一条路**：多行**粘贴**直接带换行进输入框（不走 Enter），自动长高后 Enter 发送。
- **关键技术点**：`TextArea._on_key` 内部把 enter 直映射换行，声明式 BINDINGS 拦不住 → 用子类覆写 `_on_key`。
- 此 state 仅 UI 层，不动 agent/context 逻辑；264 单测全部无关且全绿。