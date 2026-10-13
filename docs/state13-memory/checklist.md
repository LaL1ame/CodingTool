# state13 长期记忆 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。

## 实现完整性
- [ ] 指令三层发现（`discover_instruction_roots`）按「用户级→项目根→递归子目录」顺序返回，深度升序（验证：临时目录建造三层 `.mewcode/*.md` 后 `load_instructions` 顺序含三块内容）。
- [ ] `@include` 原地展开；嵌套超 depth_limit、成环、路径逃逸时只跳过该引用、主文件其余行保留（验证：造超限/环/逃逸 fixture 后输出含主内容、不含被拦引用）。
- [ ] 空指令场景不抛错，`load_instructions` 返回空串（验证：无 `.mewcode/` 的项目调用返回 `""`，不异常）。

## 笔记存储
- [ ] 四类笔记按目录分放：`preferences/corrections` → 用户级，`project_knowledge/references` → 项目级（验证：`store.append` 后对应 `.md` 出现在正确目录，返回标识字符串正确）。
- [ ] 追加精确去重：重复条目不重复落盘（验证：同文本追加两次，文件仍一条）。
- [ ] `delete(keyword)` 跨两侧删含关键词条目、幂等（验证：删两次计数第二次为 0）。

## 注入
- [ ] `build_system_prompt(...)` 增 `memory_blocks` 参数，无参调用行为不变（验证：`test_plan_prompt.py` 全绿，向后兼容）。
- [ ] 注入块落在 volatile 尾部、缓存断点后（验证：`test_memory_inject.py` 断言 `memory_blocks` 出现在 `volatile` 末位）。
- [ ] `to_index` 施加 200 行 / 25KB 上限（验证：造大量笔记 → 索引长度不超上限，字符优先）。

## 自动记忆
- [ ] 每轮自然停（done、无工具调用）触发一次后台调度（验证：`test_memory_auto` / chat 绑定测试——假 provider 收到一次调用，回调 fired）。
- [ ] 后台写不阻塞：schedule 不 await，异常被吞仅记日志（验证：假 provider 抛错 → 不向 send_message 传播、会话继续）。
- [ ] transcript 去重交 LLM 处理，坏输出不崩溃（验证：返回无法解析文本 → 脚本容错，仅记日志）。

## 看/删命令
- [ ] `/memory` 解析为 `("memory","")`、`/forget <kw>` 为 `("forget","<kw>")`（验证：`test_commands.py` 新增断言）。
- [ ] UI `/memory` 分组渲染注入内容、`/forget <kw>` 报删除条数（验证：阶段六真机，下见端到端）。

## 集成
- [ ] Agent 每轮经 `memory_injector` 拿到 volatile 块（缺省 None 行为不变）（验证：`test_agent.py` 全绿 + 注入断言）。
- [ ] Chat done Delta → `auto_memory(_msgs[:])` 快照，不被后续增量污染（验证：绑定测试断言快照在调度瞬间固定）。
- [ ] 项目无 syntax error，模块可导入（验证：`python -c "import codepilot.memory, codepilot.memory.resolver, codepilot.memory.store, codepilot.memory.auto, codepilot.memory.inject"`）。
- [ ] 全量单测通过、无 lint 回归（验证：`pytest -q` —— 应 274 既有 + 新增全绿；`ruff check .` 如配置）。

## 端到端场景
- [ ] 场景 1（注入可见）：某项目 `.mewcode/general.md` 写一条「回复务必附验收清单」→ 启动新会话 → 输入「简单介绍本项目」（此时无笔记）→ 指令内容生效、模型按指令风格回复；`/memory` 能看到该指令（验证：真机 TUI 观察回复含验收清单行为 + `/memory` 列出）。
- [ ] 场景 2（自动记忆沉淀）：同项目跑一个自然终止的会话（模型正常结束一轮）→ 等待数秒 → 退出重启 → `/memory` 可见该轮沉淀的偏好/知识类笔记文件（验证：真机观察 `/memory` 列表长出新条目 / 或直接读 `~/.codepilot/projects/<sha1>/memory/*.md`）。
- [ ] 场景 3（/forget 可控）：`/forget 一个关键词` → 再次 `/memory` 对应条目消失，命令幂等（验证：真机 + `store.delete` 单测）。
- [ ] 场景 4（双向约束无副作用）：会话正常跑工具多轮含 done → 记忆后台写入不影响 UI 与后续轮次；无指令/无记忆项目启动无异常（验证：真机 + 全量测试）。