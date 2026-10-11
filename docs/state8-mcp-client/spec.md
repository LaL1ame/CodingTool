# CodePilot MCP 客户端 Spec

> 状态:已批准(spec.md 阶段)｜编码前须经 plan/task/checklist 全部批准(HARD GATE)

## 背景

CodePilot 目前只有 6 个内置工具(read/write/edit/glob/grep/run),由 `ToolRegistry` 统一注册、Agent 无感调用。要接入外部生态的 MCP Server 工具,需要一个遵守 MCP(Model Context Protocol)协议的客户端:启动时发现外部 server 提供的工具,通过适配层包装成现有 `Tool` 接口注册进同一注册中心,Agent 调用时完全无感。

## 目标

让用户能在配置文件里声明 MCP Server 列表,CodePilot 启动时自动连接、发现工具、接入工具中心;协议与生命周期细节对 Agent 透明。

## 功能需求

- **F1(配置声明与两层合并)** 从用户级(home config)与项目级(项目 config)各读 `mcp:` 段,合并成一个 server 表,项目级覆盖用户级同名项(后盖前)。
- **F2(两种传输)** 支持 stdio(以子进程启动本地 server,走 stdin/stdout 管道)与 Streamable HTTP(连接远程 server)。
- **F3(JSON-RPC 2.0 消息)** 按 JSON-RPC 2.0 收发消息;请求携带递增 `id`,响应按 `id` 关联回对应请求,支持异步配对。
- **F4(三步会话)** 每个 server 一次会话按序执行:initialize 握手 → tools/list 发现工具 → tools/call 调用工具。
- **F5(工具发现与适配注册)** 把发现的远端工具包装成现有 `Tool` 接口,以 `{server}.{tool}` 命名注册进 ToolRegistry;Agent 通过注册表调用,无感。
- **F6(权限集成)** 包装后的 MCP 工具 `execute` 经由现有权限系统门控,manual/auto/strict 与规则对其同样生效。
- **F7(生命周期与隔离)** 启动时自动连接并注册所有 server;多个 server 连接做缓存;单个 server 连接/初始化失败不影响其他 server。
- **F8(配置值展开)** server 配置中的 `command`/`args`/`env` 值、HTTP `url`/`headers` 值支持 `${VAR}` 环境变量展开。

## 非功能需求

- **N1 隔离性**:单个 server 连接/初始化/调用失败,不影响其他 server 的发现、注册与调用;不被该故障拖垮 Agent。
- **N2 启动时序**:工具发现与注册在 Agent 开始消费工具之前完成。
- **N3 超时与错误兜底**:每次 MCP 调用有请求超时;server 返回错误/超时被转为 `ToolResult(error=…)`,Agent 正常收错误、不崩溃。
- **N4 环境变量展开来源**:`${VAR}` 一律取进程环境变量(`os.environ`);未定义的变量 → 该 server 判定为配置错误,放弃启动并告警(不含糊地带病启动)。
- **N5 无状态适配**:每次 `tools/call` 独立包装,不依赖跨调用的服务端会话状态(stateless adapter)。

## 关键设计决策(需求澄清阶段已确认)

1. **权限集成**:MCP 工具 `execute` 走**完整权限系统**(manual/auto/strict + 规则),与本地 run/write **同一种**权限确认屏。
2. **配置落脚点**:`mcp:` 段放进现有 `config.yaml`;解析时并读 home + 项目两份、map 合并、后盖前。
3. **工具命名**:远端工具注册为 **`{server}.{tool}`**,避开与本地裸名撞名,来源可追溯。
4. **副作用默认**:MCP 工具默认`side_effect=True`(串行、plan 模式不可见,保守安全);若某 server 只读,配置里标 `read_only: true` 声明。

## 不做的事(明确排除)

- MCP 的 **resources / prompts / sampling** 能力(本步只做 tools)。
- **健康检查 / 自动重连 / 心跳**。
- 运行中**热增删** server(只启动时发现)。
- MCP 协议层的权限/认证协商细节(如 OAuth),HTTP server 的鉴权仅靠配置里 `headers` 透传。

## 验收标准

- **AC1(F1)**:user config 与 project config 声明**同名** server、参数不同 → 启动用**项目级**那份(后盖前);**不同名**各自注册、互不覆盖。
- **AC2(F2)**:分别配置 stdio server 与 HTTP server,两者都能连接成功并枚举各自工具。
- **AC3(F3)**:并发发起多个调用,响应按 `id` 正确回对请求,不发生串答。
- **AC4(F4)**:连接后能列工具;调用一个能返回正确结果。
- **AC5(F5)**:远端工具以 `server.tool` 出现在 registry / Agent 可见工具中;Agent 发起调用拿到结果,对 Agent 无感。
- **AC6(F6)**:同一带副作用远端工具, auto 下静默执行、manual 下弹权限确认、strict 下被拒(与本地 run/write 同款权限屏)。
- **AC7(F7)**:故意配一个连不上的 server,其余 server 仍正常发现与调用,启动不崩。
- **AC8(F8)**:含 `${VAR}` 的 http `url` / stdio `command` / `env` 正确展开;未定义变量时该 server 被放弃并告警。

---

## 附录:MCP server 与 client 接入示意图

本图补充说明"server 建在哪 / client 怎么接入"两个问题,帮助回看架构。

```
config.yaml 的 mcp: 段
   │  load_mcp_config() 解析 + 两层合并 + ${VAR} 展开
   ▼
McpManager 拿到 server 列表
   │
   ├─ 每 server 建一个 Transport ────── 这是"管道",负责把消息传过去
   │     ├─ stdio:拉起子进程,走 stdin/stdout 管道
   │     └─ http :httpx 发 POST,Streamable HTTP
   │
   ▼
McpClient 三次握手(MCP 协议规定)
   ① initialize             双方确认身份/协议版本
   ② notifications/initialized  通知 server"我已就绪"
   ③ tools/list             问 server"你有什么工具" → 拿回 {name, desc, schema}
   │
   ▼
McpToolAdapter 把每个远端工具包装成 codepilot 的 Tool 接口
   取名 = server.tool,注册进 ToolRegistry ← Agent 从此"无感"看到它
   │
   ▼ (真正使用时)
Agent 要调某个工具
   │  _execute_one:先过权限系统(manual/auto/strict + 规则)
   ▼
adapter.execute(...) → client.call_tool(name, args)
   │  transport.request("tools/call", {...})   ← 一条 JSON-RPC 请求
   ▼
server 那边真正执行 → 结果原路返回 → 映射成 ToolResult 给 Agent
```

要点:
- **MCP server 是外部程序/远端服务,不在 codepilot 内**;配置只登记"怎么找到它"(stdio 的 command/env,或 http 的 url/headers)。
- `Transport` 决定消息怎么传(帧/SSE/id 配对);`McpClient` 决定协议次序(3 步握手)。分层效果:换一种传输,`McpClient` 无需改动。
- 本步 client 只实现 tools(不含 resources/prompts/sampling),http 传输不处理 OAuth 鉴权。