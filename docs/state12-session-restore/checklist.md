# CodePilot 会话恢复韧性 Checklist

## 写回增强
- [x] 会话 ID `YYYYMMDD-HHMMSS-xxxx`（本地时间 + 4 位 hex 防同秒撞车）。
- [x] JSONL 新增 `assistant`(带 tool_calls) 与 `tool_result` 行；`chat.py` 接在 `_record` 后写入。
- [x] 旧 JSONL（仅 meta/message/mode）向后兼容可读。

## 恢复编排
- [x] 坏行跳过（解析失败行静默跳过）。
- [x] **dangling 截断**：`assistant` 的 tool_call 无对应 `tool_result` → 该行起截断，不回灌悬空 tool_use。
- [x] **token 超限先压一次**：恢复序列超预算 → 压缩一次（不复循环）。
- [x] **时间跨度提醒**：距最后消息超 `gap_hours` → 最前插 `role=user` 提醒，保持角色交替合法。

## 元信息（不维护 meta 文件）
- [x] `summarize` 扫 JSONL 得出 id/title/message_count/last_ts。
- [x] 会话列表标题/条数据此现算，无第二份同步状态。

## 过期清理
- [x] `cleanup_expired(session_dir, max_age_days=30)` 启动回收陈旧会话；只删过期、幂等。

## 接线
- [x] `main.py` `--resume`（默认接最近会话）/`--new`；启动执行过期清理。
- [x] `config`：`session:` 段（`gap_hours`/`max_age_days`/`auto_resume`）。

## 回归与验收
- [x] `pytest -q` 264 既有 + 新增全绿。
- [ ] 真机：带工具多轮 → 中断 → `--resume` → 上下文完整还原、dangling 截断、无崩溃。

## 设计约束（保持）
- [x] 会话目录仍在用户级 `~/.codepilot/projects/<sha1>/`，不进 git。
- [x] 恢复只读幂等，除过期外不删不改原文件。
- [x] 不引入 meta 文件；`.mewcode/` 留给 state13。