# CodePilot 权限系统 Checklist

> 每一项通过运行代码或观察行为验证，聚焦系统行为。

## 实现完整性

- [ ] 五层模块各自实现且可调用（验证：`import codepilot.permissions` 及其子模块成功）
- [ ] 黑名单硬拦危险命令（验证：`hit("rm -rf /")` 命中、`hit("git status")` 不命中）——AC1
- [ ] 沙箱 `check_escape` 对 `../` 与符号链接越界返回原因（验证：sandbox 单测）——AC2
- [ ] 规则解析 `run(git *)` → tool/pattern/verdict；glob 与精确均命中（验证：rules 单测）——AC3

## 五层裁决与优先级

- [ ] **黑名单硬层**：高危命令被 deny，且尝试用 allow 规则或 permissive 档位均无法放开（验证：policy 单测）——AC1
- [ ] **路径沙箱**：file_path 越界被 deny（验证：policy 单测喂越界路径）——AC2
- [ ] **优先级** `deny > ask > allow`：同一 subject 同时命中多规则，取 deny；写在哪一层文件不影响结果（验证：policy 单测 + loader 换层重跑）——AC8
- [ ] **三档兜底**：无规则命中时 strict→deny、default→ask、permissive→allow（验证：mode/单测）——AC4
- [ ] **permissive 不覆盖显式 deny**（验证：policy 单测 permissive+deny 规则 → 仍 deny）——AC5
- [ ] **拒绝不终止循环**：被拒工具返回 error，下一轮仍执行（验证：agent 单测拒绝后继续）——AC7

## 人在回路

- [ ] ask 时调用 human 回调；`ALLOW_THIS` 仅此轮、`ALLOW_SESSION` 会话内不再问、`ALLOW_FOREVER` 写本地规则文件且后续命中自动放行（验证：policy 单测 + 触发后重查）——AC6
- [ ] `DENY` → 拒绝返回（验证：policy 单测）——AC6

## 集成

- [ ] agent 执行前调用 `permission.check`，拒绝返回「权限拒绝: reason」（验证：test_agent 拒绝场景）
- [ ] run 工具迁移前/后行为兼容：默认档无规则时仍触发确认（验证：test_run + 集成）
- [ ] `PermissionScreen` 四选映射 HumanChoice、可被 push（验证：导入 + 静态检查）
- [ ] `.gitignore` 含 `*.local.yaml`（验证：查看 .gitignore）

## 验证与测试

- [ ] 项目无语法错误、`codepilot.permissions` 导入成功（验证：`python -c "import codepilot.permissions"`）
- [ ] 全量单元测试通过（验证：`bash ci/run-tests.sh` → ALL CHECKS PASSED）——AC9

## 端到端场景

- [ ] **场景 1（黑名单）**：`/do` 下让模型执行 `rm -rf /` → 返回「权限拒绝: 黑名单命中」，模型不重试且循环不崩（验证：真机 TUI）——AC1/AC7
- [ ] **场景 2（人机永久放行）**：TUI 触发一次 ask → 选「永久允许」→ 后续相同调用自动放行、不再弹（验证：真机 + 查本地规则文件新增条目）——AC6/AC10
- [ ] **场景 3（san盘冻结路径）**：让模型 write 到 `../` 或指向目录外的软链 → 拒绝（验证：真机）——AC2
- [ ] **场景 4（模式切换）**：切 permissive 后，无 deny 规则的一般操作不询问直接执行（验证：真机或日志）——AC4/AC5