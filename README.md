# CodePilot

> 终端里的多智能体 AI 编程助手 —— 模型通过工具真实读写文件、执行命令、搜索代码，自主完成多步编码任务。

[![CI](https://github.com/LaL1ame/CodingTool/actions/workflows/test.yml/badge.svg)](https://github.com/LaL1ame/CodingTool/actions/workflows/test.yml)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Tests](https://img.shields.io/badge/tests-943%20passed-brightgreen)

CodePilot 是一款运行在终端里的 AI 编程代理（Agentic Coding Tool）：你在终端描述任务，它像一位工程师一样自主规划步骤、读写文件、搜索代码、执行命令，并通过 TUI 把整个过程实时呈现给你。协议层与 Agent 框架均为自研（不依赖 LangChain 等现成框架）。

---

## 这是什么

一个 AI 助手要真正「干活」而不是「空谈」，必须跨过几道门槛。CodePilot 的每一层设计都对应其中一道：

| 要解决的问题 | CodePilot 的答案 |
| --- | --- |
| 模型只会说话，不会动手 | 六类内置工具（read / write / edit / glob / grep / run），模型真实操作系统 |
| 长任务中途失控 | ReAct 循环 + 结构化停止判定 + 结果感知空转守卫 + 可续接检查点 |
| 权限与信任 | 五层权限防御：黑名单、路径沙箱、规则引擎、模式兜底、人机回环审批 |
| 上下文窗口溢出 | 两层压缩（大结果存盘 + LLM 结构化摘要）+ token 预算熔断 |
| 跨会话遗忘 | JSONL 会话持久化与恢复 + 长期记忆（指令注入 + 自动笔记提炼） |
| 能力重复劳动 | Skill 技能包 + Hook 生命周期钩子 + MCP 外部工具接入 |
| 多任务并行冲突 | SubAgent 委派 + Git Worktree 隔离 + Team 多智能体协作 |

---

## 核心能力

### Agent 循环与可靠性
- ReAct 循环：调用模型 → 执行工具 → 回灌结果 → 再循环，直到任务完成。
- 六类结构化停止判定：`done / limit / stall / unknown_tool / cancelled / error`，达上限或空转是可续接的暂停点（`/continue` 续跑）。
- 结果感知空转守卫：用「工具名 + 参数 + 原始结果」三元指纹区分合法轮询与真死循环，不误判、不纵容。
- 副作用工具串行栅栏 + 只读工具并发执行，兼顾安全与速度。

### 多模型接入（自研协议层）
- 手写 Anthropic / OpenAI 双协议流式适配器：SSE 行流解析、`tool_use`/`tool_result` 配对、usage 三段累积、prompt 缓存断点。
- 统一的 `Delta` 增量事件模型，界面只消费事件、不感知循环内部。

### 多智能体协作
- **SubAgent 委派**：统一 `agent` 工具把子任务交给独立子 Agent（干净上下文 / 受限工具 / 独立权限），角色定义式 + Fork 继承式两种模式。
- **Git Worktree 隔离**：每个隔离任务一个独立工作目录与分支，生命周期自动管理（24h TTL 清理、fail-closed 不回退主工作区），并行改文件互不覆盖。
- **Team 团队协作**：主 Agent 作为 Lead 创建长期小组、派生成员并行干活——三套工具面隔离、共享任务板（依赖门控原子领取）、JSONL 邮箱 + 文件锁、审批闭环、跨进程上下文恢复、git 合并收口（冲突自动回滚上报）、coordinator 专注模式。

### 上下文 / 会话 / 记忆
- 两层上下文压缩：超阈值工具结果落盘 + LLM 结构化摘要；不引 tokenizer 的「锚点 + 增量」token 估算。
- JSONL 会话落盘 + 按协议重建请求形态的恢复（`-c` / `--resume` 无缝续接，30 天过期自动清理）。
- 长期记忆：`.rules` 指令注入 + 每轮后台自动提炼四类笔记（`preference / correction / project / reference`）。

### 扩展生态
- **Skill**：三级存放（项目 > 用户 > 内置）、两阶段注入、工具白名单动态收窄、`/reload` 热重载。
- **Hook**：8 事件 / 4 动作（command / http / prompt / subagent），tool_pre 拦截通道。
- **MCP**：自研 JSON-RPC 客户端，stdio（Content-Length 定长帧）与 Streamable HTTP（JSON / SSE 双形态）传输，后台连接「就绪即用」。

### TUI 界面
基于 [Textual](https://github.com/Textualize/textual) 的异步终端界面：聊天区、实时上下文占用状态栏、子任务观察面板（F3）、放行模式切换（F2）、权限审批弹窗、多行输入（Enter 发送 / Ctrl+J 换行）。

---

## 快速开始

### 环境要求
- Python 3.10+（CI 运行于 3.11）
- 一个大模型 API Key（Anthropic Claude 或 OpenAI 兼容端点）

### 安装
```bash
git clone https://github.com/LaL1ame/CodingTool.git
cd CodingTool
pip install -e .
```

### 配置
```bash
cp config.example.yaml config.yaml
```
编辑 `config.yaml`，填入你的 Provider 与 API Key：

```yaml
providers:
  - name: "Claude Sonnet"
    protocol: anthropic          # 或 openai
    model: claude-sonnet-4-6
    base_url: https://api.anthropic.com
    api_key: sk-ant-your-key-here
```

### 运行
```bash
codepilot            # 在任意 Git 仓库根目录启动
codepilot -c         # 继续最近一次会话
codepilot --resume   # 列出历史会话选择恢复
codepilot --new      # 强制新开会话
```

---

## 使用

### 常用斜杠命令
| 命令 | 作用 |
| --- | --- |
| `/help` | 命令帮助 |
| `/plan` `/do` | 只读规划 / 恢复执行（工具轴切换） |
| `/memory` `/forget` | 查看 / 删除长期记忆 |
| `/compact` | 手动压缩上下文 |
| `/continue` | 从暂停点续跑 |
| `/agents` `/bg <id>` | 子任务列表 / 手动转后台 |
| `/worktree list` `/worktree rm <name>` | 隔离工作目录管理 |
| `/team` | 团队系统（建组 / 派人 / 任务 / 合并等） |

### 快捷键
| 键 | 作用 |
| --- | --- |
| `Enter` | 发送 |
| `Ctrl+J` | 换行 |
| `Esc` | 取消当前轮次 |
| `F2` | 循环放行模式（manual → auto → strict） |
| `F3` | 子任务观察面板 |
| `Ctrl+B` | 前台委派转后台 |

---

## 架构一瞥

`codepilot/` 包由入口 `main.py` 统一装配，分层单向依赖：

```
codepilot/
├── main.py          # 总装入口：读取配置、装配全部子系统、启动 TUI
├── agent.py         # Agent 循环：ReAct 编排 + 停止策略 + 空转守卫
├── providers.py     # Provider 抽象：Anthropic / OpenAI 双协议
├── protocols.py     # 协议层：流式 Delta、ToolCall、usage
├── chat.py          # 对话管理层
├── modes.py         # 模式状态机：PLAN/BUILD × manual/auto/strict
├── tools/           # 工具系统：Tool 基类 + 六内置工具 + 注册中心
├── permissions/     # 权限：黑名单、沙箱、规则引擎、人机回环
├── context/         # 上下文：token 估算、预算、存盘、压缩
├── session/         # 会话：JSONL 存储、恢复编排、过期清理
├── memory/          # 长期记忆：指令注入、自动笔记
├── skills/          # Skill：三级加载、白名单、双模式
├── hooks/           # Hook：事件-条件-动作模型
├── mcp/             # MCP 客户端：stdio/HTTP 传输、工具发现
├── subagents/       # 子代理：角色定义、委派、轨迹回放
├── worktree/        # Git 工作树隔离：生命周期管理
├── teams/           # 团队系统：Lead 编排、任务板、邮箱、审批、合并
├── commands/        # 斜杠命令：注册中心、解析、分发、补全
├── prompt/          # 提示词构建：稳定前缀 + volatile 尾块
└── ui/              # Textual TUI：主应用、控件、主题
```

执行链路：一条用户消息进入 `Agent.run()` 的 ReAct 循环，每轮重建分层系统提示、流式调用模型、按需执行工具（只读并发 / 副作用串行）、回灌结果，直到完成或命中停止策略；全程以事件流驱动 TUI。

---

## 测试与 CI

- **943 项自动化测试**（pytest）：`python -m pytest -q`
- GitHub Actions 在 `main` 分支 push / PR 时运行全量测试（见 `.github/workflows/test.yml`）。
- 每个功能阶段既有自动化测试，也有「真机验收」记录（headless 端到端 + TUI 场景）。

---

## 开发方式

本项目遵循 **design-first** 流程，每个功能都先写四份设计文档（`spec → plan → task → checklist`），获批后再编码：

- 阶段文档：`docs/stateNN-<topic>/{spec,plan,task,checklist}.md`（state4 ~ state20）
- 关键架构裁决：`docs/decisions.md`（ADR-001 ~ 005）
- 跨阶段进度：`SHARED_PROGRESS.md`

代码风格由 `ruff` 约束，提交信息遵循中文 Conventional Commits。

---

## 已知边界

- 成员 Agent 是同进程协程（非真实进程隔离）；无跨机器 / 跨网络协作。
- MCP 的真实 npx/node server 在 Windows 下的 spawn 兼容尚未打通（非协议缺陷，属已知限制）。
- 本地单机 CLI 工具，无服务端、无多用户共享。

---

## 依赖

极简依赖——运行时仅 3 个：

- `textual` —— TUI 框架
- `httpx` —— HTTP 客户端（模型 API 流式请求）
- `pyyaml` —— 配置解析

开发依赖：`pytest`、`pytest-asyncio`。

---

> 说明：本仓库当前尚无 LICENSE 文件，属个人学习 / 作品集项目。
