# CodePilot 会话恢复韧性 Task

## T1: 会话 ID 格式 + 排序
- [x] `store.new_session_path`：`<unix秒>.jsonl` → `YYYYMMDD-HHMMSS-xxxx.jsonl`（本地时间 + `secrets.token_hex(2)` 4 位 hex 后缀）。
- [x] `store.list_sessions`：按**文件名**降序（替代 mtime），同格式前缀兼容旧文件。

## T2: JSONL 行扩展 + 写入接线
- [x] `store` 新增 `append_assistant_toolcalls(path, *, text, tool_calls)` → 行 `{"type":"assistant","content","tool_calls",ts}`。
- [x] `store` 新增 `append_tool_result(path, traceback 对齐: *, call_id, name, output, error)` → 行 `{"type":"tool_result", ...}`。
- [x] `chat.py`：`_record` 之后、agent 每轮工具结果回灌处，写 assistant+tool_calls 与逐条 tool_result 行。

## T3: load 结构化解析 + dangling 截断
- [x] `load_session` 返回结构化段落（顺序：message/assistant/tool_result/mode/meta），坏行跳过。
- [x] 新增截断规则：某 `assistant` 行的 `tool_call_id` 在后续无对应 `tool_result` → 该行起截断（含本行），丢弃其后所有行。

## T4: summarize（扫行出元信息）
- [x] `store.summarize(path)` → `{id, title, message_count, last_ts, role_counts}`；title 取首条 user 前 N 字。
- [x] 不新增 meta 文件；会话列表/标题据此现算。

## T5: context 单发压缩入口
- [x] `context/manager.py` 暴露一次压缩入口（复用 `_do_compact(manual=True)`），供恢复时「先压一次」。

## T6: restore_chat 编排（新模块 `session/restore.py`）
- [x] `restore_chat(path, ctx_mgr, *, gap_hours)`：按序执行 坏行跳过 → dangling 截断 → token 超限压缩一次 → 时间跨度提醒。
- [x] 时间提醒：距最后 `ts` 超 `gap_hours` → 最前插 `role=user` 提醒消息，保证首末角色交替合法。
- [x] 返回 `{messages, mode, meta}`，供 `ChatManager` 重建 `_msgs`。

## T7: cleanup_expired
- [x] `store.cleanup_expired(session_dir, max_age_days=30)`：删除早于阈值的 `*.jsonl`（只删过期，幂等）。

## T8: main 接线 + config
- [x] `main.py`：启动定位最近会话；`--resume`（默认接最近）/`--new`；启动执行 `cleanup_expired`。
- [x] `config.example.yaml`：新增 `session:` 段（`gap_hours`、`max_age_days`、`auto_resume`）。
- [x] `config.py` 解析 `session:` 段并注入。

## T9: 测试 + 文档
- [ ] 单测：ID 格式/排序；坏行跳过；dangling 截断（含跨多条）；token 超限只压一次；时间提醒门限；summarize 无副作用；cleanup 只删过期。
- [ ] 旧文件（仅 meta/message/mode）向后兼容可恢复。
- [ ] `pytest -q` 264 既有 + 新增全绿。
- [ ] 更新 spec/plan/task/checklist + `SHARED_PROGRESS.md`。

## 完成判定
- 编译/导入通过；264+新增测试绿；跨重启真机还原（带工具多轮→中断→--resume）验证 dangling 截断与无崩溃。