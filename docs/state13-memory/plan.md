# state13 长期记忆 Plan

## 架构概览

新增 `codepilot/memory/` 子包，三件职责各居一层，满足 spec F1–F6：

```
┌───────────────────────────────────────────────────────────┐
│ UI 层(ui/app.py)          /memory /forget → store 读删      │
└──────────────┬────────────────────────────────────────────┘
               │ inject.injector(worskpace) 提供 volatile 块
┌──────────────▼────────────────────────────────────────────┐
│ 注入层(prompt/builder.py + memory/inject.py)                 │
│   build_system_prompt(memory_blocks=…) → volatile 尾块        │
│   agent.py 每轮调 MemoryInjector 解析当前记忆               │
└──────┬───────────────────────────────┬────────────────────┘
       │ resolve(只读拼接)             │ schedule(后台异步写)
┌──────▼─────────────┐        ┌────────▼──────────────────┐
│ resolver.py        │        │ auto.py                    │
│ 指令文件三层+@include│        │ 每轮 done→后台LLM压缩写回  │
│ (纯函数)            │        │ (provider.stream 累积)    │
└──────┬─────────────┘        └────────┬──────────────────┘
       │ 读                          │ 写
┌──────▼─────────────┐                ▼
│ store.py           │        memory/auto.py 复用 store.append
│ 四类笔记存/读/删/索引│
└────────────────────┘
```

- **读路径（每轮）**：`agent.run` 每轮调 `memory_injector()` → 拼接「指令文件(含@include) + 记忆索引」为 volatile 块 → 喂给 `build_system_prompt`。
- **写路径（自然停）**：`chat.send_message` 收到 `Delta(done=True)` → `auto.schedule(...)` 起后台 task，仓内 LLM 压一次 → 去重交 LLM → `store.append` 落盘，不 await。

## 核心数据结构

### 记忆目录布局
```
~/.codepilot/                    用户级（fs 用户主目录）
  memory/
    preferences.md / corrections.md / project_knowledge.md / references.md

~/.codepilot/projects/<sha1>/    项目级（复用 state12 会话根）
  memory/
    project_knowledge.md / references.md   # 项目知识/资料
```
四类共享常量 `CATEGORIES = ["preferences","corrections","project_knowledge","references"]`。每个文件是 Markdown，一行一个条目（bullet `- <正文>`）。用户级放 偏好/纠正；项目级放 项目知识/资料。

### Notes 结构（store 返回）
```python
@dataclass
class NoteStore:
    user_dir: Path   # ~/.codepilot/memory
    proj_dir: Path   # ~/.codepilot/projects/<sha1>/memory
    # load_notes() -> dict[str, list[str]]  # cat -> [note 正文]
    # append(cat, notes) -> int             # 追加,精确去重,返回新增数
    # delete(keyword) -> int                # 删含关键词条目,幂等,返回删除数
    # to_index() -> str                     # 组四类索引,施加行/字上限
```

## 模块设计

### `memory/resolver.py`（F1/F2，纯函数，可单测）
- `discover_instruction_roots(workspace) -> list[Path]`
  输出三层根，从最不具体到最具体：`[用户级, 项目根, *递归子目录]`。递归遍历 workspace 找 `.rules/` 目录，按深度升序（根=0 在前，深目录微后）。
- `expand_includes(md, base_dir, *, depth_limit=4, visited=None) -> str`
  处理 `@include <相对路径>`；相对 base_dir 解析；超 `depth_limit` / 已 visited / 越出指令目录树 → 跳过该处；`@include` 引用目标本身也递归展开。
- `load_instructions(workspace) -> str`
  逐根读所有 `*.md`（按文件名排序）+ 各自展开 include，按根序拼接成一段。
- 单文件读失败 → 跳过该文件并记日志，不抛。

### `memory/store.py`（F3/F5，笔记 IO + 索引）
- `NoteStore(workspace)` 构造计算 user/proj 目录（project 根复用 state12 的 sha1 规则）。
- `load_notes() -> dict[str,list[str]]`：读四类文件，切 bullet 为条目列表（缺文件→空）。
- `append(cat, notes) -> int`：对该类文件末尾追加新条目；与既有**精确去重**，返回实际新增数。
- `delete(keyword) -> int`：遍历两侧所有类，删除正文含关键词的条目，重写文件；幂等。
- `to_index(*, max_lines=200, max_chars=25000) -> str`：拼装 `[四类]` 分组索引，按 max_chars 优先、再按 max_lines 截取，符合 N1 硬上限。

### `memory/auto.py`（F4，后台异步）
- `async def run_once(provider, transcript: list[dict], existing, store) -> int`
  构造压缩提示词（四类说明 + 已有笔记供 LLM 对比去重）→ `provider.stream` 单条累积为文本 → 解析为 `{cat: [notes]}` → `store.append`，返回新增数；任一环节失败记日志并抛出（由调度方吞掉）。
- `def schedule(provider, store, transcript) -> asyncio.Task`
  `asyncio.create_task(run_once(...))` fire-and-forget 封装，内部捕获异常仅记日志。

### `memory/inject.py`（F5，volatile 块组装 + 每轮解析入口）
- `class MemoryInjector`：
  - `__init__(workspace, store=NoteStore(workspace))`
  - `build() -> list[str]`：`[指令块, 索引块]` 两段，各自施加行/字上限；无内容时返回 `[]`。
  - 可选：按文件 mtime 缓存，避免每轮重复读盘（小文件，先不缓存，profiling 后再加）。
- `__call__()` 同名，供 agent 当作 resolver 直接调用。

### 触及既有文件
- `prompt/builder.py`：`build_system_prompt(...)` 增可选参数 `memory_blocks: Sequence[str] = ()`，追加进 `volatile`（置于 plan 块之后、缓存断点后）。
- `agent.py`：`__init__` 增可选 `memory_injector: Callable[[], list[str]] | None`；每轮 build 前 `blocks = self._memory_injector() if ... else []` 传入 `memory_blocks`。Agent 不感知记忆内部——只把它当不透明 volatile 块。
- `chat.py`：`send_message` 收到 `Delta(done=True)` 时，若配了 auto-memory 调度器，`schedule(self._msgs[:])`（快照，避免与后续增量竞态）。
- `commands.py`：`/memory`→`("memory","")`，`/forget`→`("forget", rest)`。
- `ui/app.py`：`command=="memory"` → 用 store.to_index() 渲染分组信息；`command=="forget"` → store.delete(keyword) 报删除数。
- `config.py` + `config.example.yaml`：`memory:` 段，`auto_memory: true` 开关（默认开）。
- `main.py`：构建 `MemoryInjector`，注入 Agent（resolver）与 ChatManager（schedule 回调），按 `auto_memory` 开关装配。

## 模块交互 / 数据流

**注入（每轮）**
```
agent.run(round i)
  ├ memory_injector()  ─────────────► inject.build()
  │                                    ├ resolver.load_instructions()   → 指令块
  │                                    └ store.to_index()               → 索引块
  └ build_system_prompt(env, plan_mode, round, refresh, memory_blocks=…) → volatile[env,plan?,指令,索引]
```
**自动记忆（自然停）**
```
chat.send_message 收到 done Delta
  └ auto.schedule(provider, store, self._msgs[:])
      └ asyncio.create_task(run_once(...))   # 不 await,不阻塞
          ├ provider 压缩本轮 → 0..N 条
          ├ 交 LLM 对既有笔记去重
          └ store.append → 落盘
```
**看/删（命令）**
```
ui 输入 /memory → store.to_index() → 分组展示
ui 输入 /forget 关键词 → store.delete(keyword) → 报删除数
```

## 文件组织
```
codepilot/
  memory/
    __init__.py
    resolver.py     # 指令三层发现 + @include 展开(纯)
    store.py        # 四类笔记 读/写/删/索引 + 目录布局
    auto.py         # 自动记忆后台 LLM 编排(异步)
    inject.py       # MemoryInjector: volatile 块组装 + 每轮解析入口
  prompt/builder.py # +memory_blocks 参数(改)
  agent.py          # +memory_injector 可选(改)
  chat.py           # done→auto.schedule(改)
  commands.py       # +/memory /forget(改)
  ui/app.py         # +两命令处理(改)
  main.py           # 装配(改)
  config.py         # +memory.auto_memory(改)
  config.example.yaml  # 文档(改)
tests/
  test_memory_resolver.py   # F1/F2 单测
  test_memory_store.py      # F3/F5 单测
  test_memory_auto.py       # F4 单测(消息→笔记,吞错)
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 记忆块注入位置 | volatile 尾块(缓存断点后) | 记忆随会话/文件变化,不应污染 stable 前缀缓存;对齐现有 plan 块机制 |
| Agent 如何拿记忆 | 注入 `memory_injector: Callable→list[str]` | 低耦合:Agent 只认「不透明 volatile 块」,不 import memory 内部 |
| 自动记忆触发点 | chat 层收 done Delta 后 schedule | Agent 保持纯 ReAct 循环(SRP),编排层负责副作用 |
| 后台写 | `asyncio.create_task` fire-and-forget,内含 try/except | 满足 N2 不阻塞;失败仅记日志 |
| 去重策略 | LLM 对比既有笔记做语义去重 + append 时精确去重兜底 | 避免维护本地指纹表;精确去重作为确定性兜底 |
| 记忆 LLM 调用 | 复用 provider.stream 累积(不新增 provider 方法) | 尽量不改 provider 接口 |
| 注入上限 | to_index 按 `max_lines=200 / max_chars=25000` 截取 | 硬上限走 N1,字符优先于行数 |
| 指令@include | depth_limit=4 + visited 集 + 限同一指令树 | F2 防环/防逃逸,单败仅跳过 |
| 配置 | 仅 `memory.auto_memory` 开关 | YAGNI:上限做常量,不加可调档位 |

**接口完整性自检**：`build_system_prompt` 增默认参数向后兼容既有测试；`agent/chat` 增注入为可选参数，缺省行为不变（无记忆时等价现有）；memory 模块不 import ui/prompt，只依赖 `provider + 文件系统`。