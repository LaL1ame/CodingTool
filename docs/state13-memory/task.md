# state13 长期记忆 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `codepilot/memory/__init__.py` | 子包导出 |
| 新建 | `codepilot/memory/resolver.py` | 指令三层发现 + `@include` 展开（纯） |
| 新建 | `codepilot/memory/store.py` | 四类笔记 读/写/删 + 索引 + 目录布局 |
| 新建 | `codepilot/memory/auto.py` | 自动记忆后台 LLM 编排（异步） |
| 新建 | `codepilot/memory/inject.py` | MemoryInjector：volatile 块组装 |
| 修改 | `codepilot/prompt/builder.py` | `build_system_prompt` 增 `memory_blocks` 参数 |
| 修改 | `codepilot/agent.py` | 增可选 `memory_injector`，每轮传入 |
| 修改 | `codepilot/chat.py` | done Delta → `auto.schedule` |
| 修改 | `codepilot/commands.py` | +`/memory` `/forget` 解析 |
| 修改 | `codepilot/ui/app.py` | +两命令处理 |
| 修改 | `codepilot/config.py` | +`memory.auto_memory` |
| 修改 | `config.example.yaml` | `memory:` 段文档 |
| 修改 | `codepilot/main.py` | 装配 MemoryInjector + 调度器 |
| 新建 | `tests/test_memory_resolver.py` | F1/F2 单测 |
| 新建 | `tests/test_memory_store.py` | F3/F5 单测 |
| 新建 | `tests/test_memory_auto.py` | F4 单测 |
| 新建 | `tests/test_memory_inject.py` | F5 注入到 build_system_prompt |

## 执行顺序

```
T1(resolver) → T4(inject) ─────────→ T5(agent/chat 接线)
T2(store)  ───────────────────────────┴→ T7(commands/ui)
                ↘ T6(auto, 需T2)
T3(builder, 独立) → 可与 T1/T2 并行
最后:  T8(config/main/config.example) → 全量跑测试
```

---

## T1: resolver — 指令三层发现 + @include（F1/F2）

**文件：** `codepilot/memory/resolver.py`
**依赖：** 无
**步骤：**
1. 定义 `CATEGORY_ROOT_NAME = ".mewcode"`。
2. `discover_instruction_roots(workspace: Path, user_root: Path | None = None) -> list[Path]`：
   - 用户级：`user_root` 默认 `Path.home()/".mewcode"`（可注入，便于测试不碰真 home）。
   - 项目级：递归扫 workspace 下所有 `.mewcode/` 目录，按目录深度升序（根=0 最前，深目录在后）。去重、排除用户级。
   - 返回 `[用户级, *项目各级]`。
3. `expand_includes(text, base_dir, *, depth=0, depth_limit=4, visited=None) -> str`：
   - 按行扫描 `@include <rel>`；解析失败处只跳过该行，其余行保留。
   - 目标路径 = `(base_dir / rel).resolve()`；若不在 `base_dir` 所属的 `.mewcode` 树内（用 resolve 后以某一 `.mewcode` 父目录作边界）→ 跳过（防逃逸）。
   - `depth >= depth_limit` 或目标已在 `visited` 或读取失败 → 跳过。否则递归展开。
4. `load_instructions(workspace) -> str`：逐根读该根下所有 `*.md`（按文件名排序）+ 各自 expand，按根序拼接；单文件读失败静默跳过并 `logging.getLogger("codepilot.memory").warning(...)`。
5. 空目录/无文件 → 返回 `""`。

**验证：** 建临时目录测三层顺序、depth_limit 截断、环引用、逃逸拦截；`pytest tests/test_memory_resolver.py -v` 绿。

---

## T2: store — 四类笔记 IO + 索引（F3/F5）

**文件：** `codepilot/memory/store.py`
**依赖：** 无
**步骤：**
1. `CATEGORIES = ("preferences","corrections","project_knowledge","references")`；`CATEGORY_LABELS` 映射中文名。
2. `NoteStore.__init__(workspace)`：`self.user_dir = Path.home()/".codepilot/memory"`；`self.proj_dir = store.session_dir(workspace)/"memory"`（复用 `codepilot.session.store.session_dir`）。
3. `load_notes() -> dict[str,list[str]]`：两侧各类文件（`f"{cat}.md"`）读为 bullet 列表（`- ` 前缀剥离）；缺文件→空列表。
4. `append(cat, notes) -> tuple[str,int]`：目标 = 用户级（`preferences`/`corrections`）或项目级（`project_knowledge`/`references`）；对既有**精确去重**后追加；建目录；返回 `(category_targeted, added_count)`；返回字符串标识写到哪侧。
5. `delete(keyword) -> int`：遍历两侧四类文件，删正文含 keyword 的 bullet，重写；幂等；返回删除总数。
6. `to_index(*, max_lines=200, max_chars=25000) -> str`：拼 `[类别] 条目…` 分组；先按 `max_chars` 截断，再按 `max_lines` 截断（字符优先）。

**验证：** 注入 `user_dir`/`proj_dir` 用临时目录；测分类落盘位置、精确去重、删除幂等、索引上限。`pytest tests/test_memory_store.py -v` 绿。

---

## T3: builder — `memory_blocks` 参数（F5 注入点）

**文件：** `codepilot/prompt/builder.py`
**依赖：** 无（可与 T1/T2 并行）
**步骤：**
1. 签名 `build_system_prompt(env, *, plan_mode, round_num, refresh_every, memory_blocks: Sequence[str] = ())`。
2. `volatile = [format_env(...)]`；`volatile.extend(_plan_blocks(...))`；`volatile.extend(memory_blocks)`。
3. `from collections.abc import Sequence` import。

**验证：** `pytest tests/test_plan_prompt.py -v` 仍绿（默认参数向后兼容）；`tests/test_memory_inject.py` 里断言 `memory_blocks` 出现在 `volatile` 尾部。

---

## T4: inject — MemoryInjector（F5 组装）

**文件：** `codepilot/memory/inject.py`
**依赖：** T1、T2
**步骤：**
1. `class MemoryInjector`：`__init__(self, workspace, store: NoteStore)`（store 默认 `NoteStore(workspace)`）。
2. `build() -> list[str]`：`blocks = []`；`inst = load_instructions(workspace)` 非空 → 追加；`idx = store.to_index()` 非空 → 追加；返回 `blocks`。
3. `__call__() -> list[str]` 委托 `self.build()`，供 agent 当 resolver。
4. `__init__.py` 导出 `MemoryInjector`、`resolver`、`store`、`auto`。

**验证：** 临时目录放一层指令 + 一条 note → `injector.build()` 返回两段；空目录返回 `[]`。`pytest tests/test_memory_inject.py -v` 绿。

---

## T5: agent + chat 接线（读/写路径）

**文件：** `codepilot/agent.py`、`codepilot/chat.py`
**依赖：** T4（读注入）、T6（写 auto，可后置到同一步顺序内）
**步骤：**
1. `agent.py`：`__init__` 增 `memory_injector: Callable[[], list[str]] | None = None`；存 `self._memory_injector`。
2. `agent.run` 建 system prompt 处：`blocks = self._memory_injector() if self._memory_injector else []`；传给 `build_system_prompt(..., memory_blocks=blocks)`。
3. `chat.py`：`__init__` 增 `auto_memory: Callable[[list[dict]], None] | None = None`（调度回调）。
4. `chat.send_message` 主循环收 `Delta`：`if d.done and self._auto_memory: self._auto_memory(self._msgs[:])`（快照）。

**验证：** `tests/test_agent.py`、`tests/test_chat.py` 仍绿（缺省为 None 行为不变）；新增断言：注入 `memory_injector` 后 run 时 `volatile` 含该块；done delta 触发回调一次、快照不被后续增量污染。

---

## T6: auto — 后台 LLM 编排（F4）

**文件：** `codepilot/memory/auto.py`
**依赖：** T2（store）
**步骤：**
1. `def _summary_prompt(self)`：发：给四类说明 + 已有 `store.load_notes()` 文本 + 本轮 transcript（截尾至上限,避免超长），要求输出 `cat: 条目` 结构。返回 prompt 字符串。
2. `async def run_once(provider, store, transcript, *, limit=8000) -> int`：
   - `text = ""`；`async for d in provider.stream(messages=[{role:"user",content:prompt}], tools=None, system=None)` 累积 `d.text`；`d.error` → raise。
   - `_parse_notes(text)` 从输出剥离 `偏好:`/`纠正:`/`项目知识:`/`参考:` 段 → `{cat: [notes]}`；跳过无法识别的行。
   - 逐 `cat` `store.append(cat, notes)`；返回新增总数。
   - 外层 `try/except` 记日志并 re-raise。
3. `def schedule(provider, store, transcript) -> asyncio.Task`：`asyncio.create_task(_safe(run_once(...)))`，`_safe` 捕获异常仅 `logging.warning`。
4. 测试用假 provider：返回固定分段的 stream。

**验证：** `pytest tests/test_memory_auto.py -v` 绿：正常解析落盘、坏输出不崩溃、schedule 抛错被吞。

---

## T7: 命令 — /memory /forget（F6）

**文件：** `codepilot/commands.py`、`codepilot/ui/app.py`
**依赖：** T2（store）
**步骤：**
1. `commands.py`：`if first == "/memory": return ("memory","")`；`if first == "/forget": return ("forget", rest.strip())`（加进现有链）。
2. `app.py`：`command == "memory"` → `log.write(injector 的索引按类分组)`（info 样式，可复用 store.to_index 或经 inject.build 取指令+索引）；`command == "forget"` → `n = store.delete(rest)`，`log.write(f"已删除 {n} 条记忆条目")`。
3. app 需持有 `store`（构造时注入）；在命令解析链开头分支，早于普通消息提交。

**验证：** `pytest tests/test_commands.py -v` 绿（新增 /memory、/forget 解析）；UI 手工待阶段六。

---

## T8: config + main 装配 + 全量回归

**文件：** `codepilot/config.py`、`config.example.yaml`、`codepilot/main.py`
**依赖：** T4、T5、T6、T7
**步骤：**
1. `config.py`：`@dataclass class MemoryConfig: auto_memory: bool = True`；`load_memory_config(path)` 读 `memory.auto_memory`（缺省 True）。
2. `config.example.yaml`：加 `memory:\n  auto_memory: true  # 每轮自然停后后台记录偏好/知识`.
3. `main.py`：`mc = load_memory_config(path)`；`injector = MemoryInjector(workspace)`；构造 `Agent(..., memory_injector=injector)`；`auto_memory_cb = (lambda ms: auto.schedule(provider, NoteStore(workspace), ms)) if mc.auto_memory else None`→ 传 `ChatManager(..., auto_memory=...)`；装配进 App。

**验证：** `pytest -q` 全量绿（既有 264 + 新增 memory 测试，共应 ≥ 274+N）。

---

## 执行顺序图

```
T3(builder)
T1(resolver) → T4(inject) → T5(agent/chat)
T2(store)  ──────────────┤
                    ↘ T6(auto) ─┘
T2 ───────────── T7(commands/ui)
全部 → T8(config/main) → pytest 全量
```