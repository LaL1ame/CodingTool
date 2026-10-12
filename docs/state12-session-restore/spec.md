# CodePilot 会话恢复韧性（Session Restore Robustness）Spec

## 目标
让「中断之后能平滑接着用」真正成立：会话**追加写**可靠、**恢复**能容忍坏行/残缺工具调用并控制 token 与时间跨度、**过期会话**定期清理。重启后对接最近会话，而不是永远从零开始。

> 本 state 只做「写回 + 恢复」的韧性；「重启后注入项目指令/自动记忆」属 **state13**（本项目第一阶段决策：拆两个 state）。

## 现状与缺口（对照 `codepilot/session/store.py`）
已有：
- JSONL 追加写（`_write_line`）、崩溃只丢最后一行 ✅
- 恢复时坏行跳过（`load_session` 里 `json.JSONDecodeError: continue`）✅
- 会话定位在**用户级** `~/.codepilot/projects/<sha1(cwd)>/`（决策：重克隆/换目录不丢；对话不进 git）✅

缺口（本 state 要补）：
1. **未持久化工具调用行**：目前只写 `meta/message/mode` 三类行；恢复无法还原带工具调用的 LLM 序列 → 跨重启 resume（state10 的 paused(limit)+续接）在重启后丢掉工具上下文。
2. **会话 ID 用 `<unix秒>.jsonl`**：同秒新建会撞文件名。
3. **恢复无异常分级处理**：没有「工具调用没配结果→截断」「token 超限先压一次」「隔久插入时间跨度提醒」。
4. **无过期清理**：30 天以上陈旧会话不回收。
5. 恢复需元信息（ID/标题/消息数）时得**另维护 meta 文件**——违背「少一份同步状态」。

## 已确认的架构决策（本阶段拍板）
- **会话目录保持用户级** `~/.codepilot/projects/<sha1>/`，仅升级格式与链路。
- **目录职责切分**：人写的→ `.mewcode/`（state13）；机器生成的（会话/笔记）→ `~/.codepilot/`。本 state 只动会话。
- **拆两个 state**：state12=会话恢复韧性（本文档）；state13=指令文件 + 自动记忆 + 注入。

## 设计

### D1. 会话 ID 格式：`YYYYMMDD-HHMMSS-xxxx`
`YYYYMMDD-HHMMSS` 取本地时间，`xxxx` 为 4 位十六进制随机后缀（`secrets.token_hex(2)`）防同秒撞车。
- 可读、可排序（文件名即时间序，`sorted` 即时间排序，替代 mtime 排序）。
- 替换 `new_session_path` 的 `<ts:.0f>.jsonl`；`list_sessions` 改为按文件名降序。

### D2. JSONL 行扩展：补齐工具调用与结果
保留 `meta / message / mode`；新增两类行，使恢复能**核对工具配对**并重建完整 LLM 序列：

```
# 带工具调用的助手轮（替代纯文本的 message 行）
{"type":"assistant","content":"...","tool_calls":[{"id":"..","name":"..","arguments":{...}}],"ts":..}
# 每一条工具调用的执行结果（可能多条，顺序对应）
{"type":"tool_result","tool_call_id":"..","name":"..","output":"..","error":null,"ts":..}
```
- `message` 行（role=user）与新增 `assistant` 行分别代表纯文本两侧；`mode` 存放行策略快照（沿用）。
- 写入时机接在 `chat.py._record` 之后、agent 每轮工具结果回灌处：`store.append_round(...)` 一次性写 assistant+tool_calls，随后每条结果 `append_tool_result(...)`。
- 错误侧：`error` 非空时 `output` 可为空；`truncated` 信息缺省为 false（不额外维护）。

### D3. 恢复管线（`load_session` → `restore_chat` 两级）
`load_session(path)` 只负责**解析原样**，返回按行序还原的结构化段落 + 元信息；`restore_chat(path, context_manager)` 负责**编排**（截断/压缩/提醒）。职责分层清晰。

`restore_chat` 步骤（按序）：
1. **坏行跳过**：解析失败行跳过（沿用）。
2. **缺失工具截断**：顺序重建消息序列时，遇到某条 `assistant` 的某个 `tool_call_id` 在后续**无对应 `tool_result`** → 该 `assistant` 行起**截断**（含本行），丢弃其后所有行。保证不回灌「悬空的 tool_use」，维持 Anthropic 角色交替合法（对齐现有 compactor 的合法性约束）。
3. **token 超限先压一次**：重建出的 `_msgs` 估算 token 超预算 → 调 `ContextManager` 做**一次**压缩（复用现有 `_do_compact(manual=True)` 单发）。不循环压缩，压一次即恢复边界内。
4. **时间跨度提醒**：上次会话最后消息 `ts` 距现在超过阈值（默认 `gap_hours`，config 可配）→ 在恢复序列**最前**插入一条 `role=user` 的提醒消息（内容形如「距上次会话已过去 N 天……」）。置最前保持首末角色交替合法（首个真实消息是 assistant 时为非法）。
5. 返回 `{messages, mode, meta}` 供 `ChatManager` 重建。

### D4. 元信息「扫 JSONL 算」，不维护 meta 文件
新增 `summarize(path)`：扫一遍行，返回 `{id, title, message_count, last_ts, role_counts}`。
- `id` = 文件名；`title` = 第一条 user 消息前 N 字；`message_count` = message+assistant 行数。
- 会话列表页需要标题/条数时直接扫，不另存 meta（少一份要同步的状态）。

### D5. 过期清理
`cleanup_expired(session_dir, max_age_days=30)`：启动时扫该 cwd 会话目录，删除 `mtime`（或文件名时间）早于 `now - max_age_days` 的 `*.jsonl`。默认 30 天，config 可配。

### D6. 接线与安全
- `main.py` 启动：定位最近会话 → `restore_chat` 恢复 → 若为 disable/open（放开了再处理，默认行为）用一个 flag `--resume|--new` 控制；同时 `cleanup_expired` 回收。
- 只读恢复：不覆盖、不删除除过期外的任何会话文件（幂等，可重放）。
- 兼容：旧 `.jsonl`（仅 meta/message/mode）仍可恢复——新增行缺省视为无工具调用，dangling 规则对旧文件自然也成立。

## 验证
- 单测：ID 格式/排序；好行跳过；坏行跳过；dangling tool 截断（含跨多条）；token 超限压缩只压一次；时间提醒门限；过期清理；summarize 无副作用。
- 现有 264 测试全绿（store 改动向后兼容，agent/context 纯增量接线）。
- 真机：跑一段带工具的多轮会话 → 中断 → 重启 `--resume` 最近会话 → 上下文还原、dangling 被截、无崩溃。

## 影响文件
- `codepilot/session/store.py` — ID 格式、新增行类型、`load_session` 返回结构化段落、`summarize`、`cleanup_expired`。
- `codepilot/session/restore.py`（新）— `restore_chat` 编排（截断/压缩/提醒）。
- `codepilot/chat.py` — 写工具调用/结果行接在 `_record` 后；重建 `_msgs` 走 `restore_chat`。
- `codepilot/main.py` — 启动恢复最近会话 + 过期清理 + `--resume/--new`。
- `codepilot/context/manager.py` — 暴露单发压缩入口（或少改动，复用 `_do_compact(manual=True)`）。
- `config.example.yaml` — 新增 `session:` 段（`gap_hours`、`max_age_days`）。
- 更新 `docs/state12-session-restore/{plan,task,checklist}.md`、`SHARED_PROGRESS.md`。