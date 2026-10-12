# CodePilot 会话恢复韧性 Plan

## 总览
在既有 JSONL 追加写之上，构建「写回（工具调用持久化）→ 恢复（坏行/dangling/token/时间跨度）→ 清理（过期回收）」闭环。不动 agent/context 核心逻辑，只做 session 落点增强 + 恢复编排 + 启动接线。

## 阶段
**阶段一：写回增强（store 扩展）**
- D1 会话 ID 格式 `YYYYMMDD-HHMMSS-xxxx` + 按文件名排序。
- D2 JSONL 新增 `assistant`（带 tool_calls）与 `tool_result` 行；`chat.py` 接在 `_record` 后写入。

**阶段二：恢复编排（restore 新模块）**
- D3 `load_session` 结构化解析 + `restore_chat`：dangling 截断 → token 超限压一次 → 时间跨度提醒。
- D4 `summarize` 扫行出元信息（不维护 meta 文件）。

**阶段三：清理 + 接线**
- D5 `cleanup_expired` 过期回收。
- D6 `main.py` `--resume/--new` + 启动还原 + 过期清理；context manager 暴露单发压缩入口。

**阶段四：测试 + 文档**
- 新单测覆盖 D1-D5；264 全绿回归；更新 spec/plan/task/checklist + SHARED_PROGRESS。

## 优先级顺序（任务依赖）
T1(ID 格式) → T2(行类型+写入) → T3(load 结构化解析+dangling 截断) → T4(summarize) →
T5(token 超限压缩接管入口) → T6(restore_chat 编排) → T7(cleanup_expired) → T8(main 接线+config) → T9(测试+文档)。

## 关键前置依赖
- 复用 `codepilot/context/manager.py` 的 `_do_compact(manual=True)`（单发压缩），不新增压缩逻辑。
- 复用 `codepilot/session/store.py` 的 `_write_line` 追加写与 `session_dir` 定位；不引进新的存储层。
- dangling 截断规则对齐 compactor 的「Anthropic 角色交替合法」约束——截断后保证不回灌悬空 tool_use。

## 验收标准
- 跨重启：跑带工具多轮 → 中断 → `--resume` → 上下文完整还原、dangling 被截、崩坏行被跳。
- 旧会话文件（仅 meta/message/mode）向后兼容可恢复。
- 264 既有测试 + 新增单测全绿。
- 不引入 meta 文件，元信息一律扫 JSONL。

## 设计约束（不得破坏）
- 会话目录保持在 `~/.codepilot/projects/<sha1>/`（用户级，不进 git）。
- 恢复只读幂等：除过期会话外不删不改原文件。
- 机器生成数据（会话/笔记）归 `~/.codepilot/`；`.mewcode/` 留给 state13 的手写指令。