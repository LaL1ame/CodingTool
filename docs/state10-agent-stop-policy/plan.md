# CodePilot Agent 停止判定增强 Plan

> 状态：plan.md 阶段｜基于已批准 spec.md
> 改动集中于 agent.py 停止判定 + Delta 协议 + app/ChatManager 续接与提示，不新增子包。

## 架构概览

```
protocols.py(Delta新增字段) → agent.py(停止判定核心) → chat.py(续接) → ui/app.py + commands.py(展示/续接)
config.py(AgentConfig增量) → agent.py(读空转参数)
```

**关键决策**：空转检测做成 agent 内 `_StallDetector` 私有小类——只依赖回合内调用轨迹，无外部耦合，拆独立模块反而增加认知负担。续接职责由 UI/ChatManager 决定（手动 `/continue`），agent 只产出 paused + 保持 `_msgs` 完整。

## 模块设计

### protocols.py — Delta 增量字段
```python
@dataclass
class Delta:
    ...
    paused: bool = False            # F2:目标暂停(可续接)
    pause_reason: str | None = None # "limit" | "stall" | "unknown_tool"
    stall_hint: str | None = None   # F3:空转软提示(非 None 即命中)
    resume_round: int | None = None # F2:已达轮数
```

### config.py — AgentConfig 增量
```python
@dataclass
class AgentConfig:
    max_rounds: int = 18            # 原 10 → 18(F1)
    max_unknown: int = 2
    prompt_refresh_every: int = 3
    # —— state10 空转检测参数(N4)——
    stall_enabled: bool = True
    stall_repeat_k: int = 2         # 最近窗口内同签名 ≥2 记重复
    stall_window: int = 3           # 检测窗口轮数
    stall_msg: str = "检测到重复调用且无进展,建议更换工具或参数,或重新明确目标。"
```

### agent.py — _StallDetector + 停止判定重构
```python
class _StallDetector:
    def __init__(self, *, repeat_k: int, window: int) -> None: ...
    def record(self, tool_calls, had_text: bool) -> bool:
        # True=触发软 stall_hint;内部按指纹记窗口
    def should_stop(self) -> bool:     # 超过阈值 → 硬停
    @staticmethod
    def _fingerprint(tc: ToolCall) -> str:  # (name + repr(arguments)) hash
```
停止判定重构：
- 原 line 161「max_rounds → error」改为「paused(reason=limit)」
- consecutive_unknown 从 error 改 paused(reason=unknown_tool)（N1 友好化）
- 回灌前 `record` → stall_hint / should_stop → paused(reason=stall)

### chat.py — 续接支持
`send_message` 不重置 `_msgs`(已是持久)；需「续接」分支：沿用 _msgs 再触发不做 UI 污染。

### commands.py — /continue 解析
```python
if first == "/continue": return ("continue", "")
```

### ui/app.py — 展示 + 续接
- `_process_chat` 处理 `d.paused`/`d.stall_hint`(非 error,N1)
- `/continue` 分支:沿用 self._chat._msgs 再 run_worker

## 模块交互

### 停止判定决策流
```
agent.run 每轮:
  if 无 tool_calls → done ✅
  if unknown>阈值 → paused(reason="unknown_tool")
  if round >= max_rounds → paused(reason="limit"); return(保 _msgs)
  stall_hit = detector.record(...)
  if stall_hit → stall_hint(继续) elif should_stop → paused(reason="stall"); return
  执行工具 → 回灌 → 下一轮
```

### 续接流程
```
UI 收 paused(limit) → 显示"/continue 续跑"(非 error)
用户 /continue → ChatManager 保留同 _msgs → 再 run → 续跑
下一轮達限 → 再 paused → 可再续(F2.3)
```

### 停止原因 → UI 映射
| reason | Delta | UI |
|--------|-------|----|
| done | done=True | 完成✅ |
| limit | paused(reason=limit) | "/continue 续跑"(dim) |
| stall | paused(reason=stall) | "检测到持续空转已暂停"(dim) |
| unknown_tool | paused(reason=unknown_tool) | 友好提示 |
| cancelled | cancelled=True | "已取消" |
| error | error="…" | 红色(仅真错) |

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 超限停止 | paused 非 error | F2 对齐 Claude Code |
| 空转位置 | agent 内 _StallDetector | 只依赖回合轨迹 |
| 空转判据 | lex(名+参数hash)+无文本 | F3.4 确定、零依赖 |
| soft→hard | hint(继续)→持续→paused | 先给换策略机会 |
| stall_hint 表达 | 字段非 None 即命中 | 避免字段爆炸 |
| 续接 | 手动 /continue | 避免隐藏失控 |
| 续接状态 | 同一 _msgs 不重放副作用 | N2 |
| 上限 | 18 不学干级 | 平衡够用/不做子任务 |
| 停止分类 | 枚举 reason | 偏离"全当 error" |
| 兼容 | 新字段默认 None/False | N5 |

## 文件组织
```
codepilot/
├── agent.py      (M) 停止判定重构 + _StallDetector
├── config.py     (M) AgentConfig
├── protocols.py  (M) Delta 字段
├── commands.py   (M) /continue
├── chat.py       (M) 续接沿 _msgs
├── ui/app.py     (M) paused/stall 展示 + /continue
└── tests/
    ├── test_agent.py      (M) F2/F3/F4 单测
    ├── test_commands.py   (M) /continue
    └── test_agent_resume.py (N) N2 续接集成
```