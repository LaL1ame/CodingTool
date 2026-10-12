# CodePilot 多行输入（TextArea 改造）Checklist

## 实现
- [x] compose 用 `TextArea(id="prompt")`，非单行 `Input`。
- [x] 提示行 `#prompt-hint` 展示提交/换行说明。
- [x] App 绑定 ctrl+enter → `action_submit`。

## 行为（AC）
- [x] 多行输入：Enter 换行，可编辑/粘贴 >1 行（未截成首行）——挂载冒烟验证 lines=3。
- [x] Ctrl+Enter 发送：读 `.text`、清空、统一 `_submit` 分发。
- [x] 纯命令路由：`/mode` `/exit` `/plan` `/do` `/compact` `/continue` `/edits` 走 `_submit`（冒烟验证 `/mode` 不炸）。
- [x] 流式/禁用状态：`_submit` 置 `inp.disabled=True`，`_process_chat` finally 恢复（沿用原逻辑）。
- [x] 输入区定高 3、超长内部滚动（theme.tcss `#prompt { height: 3 }`）。

## 回归
- [x] 无残留 `Input(id="prompt")` / widget `.value` 引用（grep）。
- [x] import/compileall 冒烟 OK。
- [x] `pytest -q` → 264 passed（无回归）。

## 待人工（真机）
- [ ] 粘多行 prompt → Ctrl+Enter 完整进入对话。
- [ ] 短命令 `/exit`、`/continue`、`/compact` 用 Ctrl+Enter 可用。
- [ ] 输入框 3 行渲染、超长内部滚动正常。

## 备注
- 提交键决策：B（Ctrl+Enter 发送，Enter 换行），用户已确认。
- 此 state 仅 UI 层，不动 agent/context 逻辑；264 单测全部无关且全绿。