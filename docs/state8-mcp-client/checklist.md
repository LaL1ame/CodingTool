# CodePilot MCP 客户端 Checklist

> 验收结论:`假 server`端到端通过,协议端到端成立;**真实 npx/node server(Windows)接入为已知限制/后续**。

## 实现完整性
- [x] `codepilot/mcp/` 子包可导入,`McpManager`/`McpToolAdapter` 暴露。
- [x] `config.load_mcp_config` 两层合并 + `${VAR}` 展开,`McpServerSpec` 字段齐全。
- [x] stdio/http 两种传输实现 `connect/request/notify/close`。
- [x] `McpClient` 三步握手:initialize → list_tools → call_tool。
- [x] `McpToolAdapter.execute` 返回 `ToolResult`,`name` 为 `server.tool`。
- [x] `McpManager.connect_all` 把发现的工具注册进 `ToolRegistry`。

## 功能/验收(AC1–AC8)
- [x] **AC1**:user/project 同名 server 项目覆盖用户;异名各自注册(单测)。
- [x] **AC2**:stdio 枚举工具(Mock/假 server);http 单 JSON + SSE 解析(单测)。
- [x] **AC3**:并发调用按 id 配对不串答(5 并发单测)。
- [x] **AC4**:connect 后列工具、调用返回正确结果。
- [x] **AC5**:远端工具以 `server.tool` 出现在 registry;调用无感。
- [x] **AC6**:manual/auto/strict 对 MCP 工具生效(经 `Agent._execute_one` 统一门控)。
- [x] **AC7**:坏 server 跳过、不影响其它、启动不崩。
- [x] **AC8**:`${VAR}` 展开、未定义即放弃该 server 并告警(单测)。

## 权限集成
- [x] 无额外权限代码:注册即被 `Agent._execute_one` 统一门控,零成本达成 F6。

## 验证与测试
- [x] 全量 `pytest -q` → **211 passed**(44 个 MCP 新测试 + 原有用例无回归)。
- [x] import 接线通过。

## 端到端(实际记录)
- [x] 假 stdio server(`srv.sum` → 15):发现 + 调用 + 无感,协议端到端成立。
- [x] 权限三档:auto 静默 / manual 弹权限屏 / strict 拒绝。
- [x] 坏 server 隔离:跳过、告警、不崩。
- [ ] **真实 npx/node server(Windows)** —— **已知限制未打通**;根因在 `cmd.exe/c npx` 的 spawn 侧兼容(stdout 污染 / CRLF 转换),非 client 协议错误。独立后续,不视为 state8 缺陷。

## 已迭代修复(超初版)
- [x] Windows `.cmd/.bat` 启动兼容(经 `cmd.exe /c`)。
- [x] MCP 后台连接,不阻塞 TUI 启动(修正"启动同步等连接"的设计缺陷)。
- [x] 失败 server 传输清理(try/finally 兜底,防子进程泄漏告警)。
- [ ] 退出告警:移除外部 server 配置后应消除,待一次真机运行确认。