---
name: commit
description: 基于未提交改动生成符合项目规范的 Git 提交信息
mode: shared
tools: [read, grep, run, edit, write]
---
# 生成 Git 提交

按以下 SOP 为当前未提交的改动起草并落地一条提交信息。

1. 先读现状：`git status --short` 与 `git diff`（`git diff --staged` 看已暂存）确认改动范围；用 read/grep 查看被改文件的意图。
2. 若项目根有 `.rules/` 或 `CLAUDE.md` 里的提交约定（本项目要求中文 Conventional Commits：`feat/fix/refactor/docs/test/chore` 前缀），遵从之。
3. 结论先行：提交信息主体写明「改了什么」与「为什么改」；必要时附简短正文点关键风险和影响面，但保持简洁。
4. 不要为了凑条数拆分提交；一个逻辑变更一个 commit。

最终给出可直接执行的提交命令（含 `git add` 范围），{input}。如用户未指定则自选合理范围。