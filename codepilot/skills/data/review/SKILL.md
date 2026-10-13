---
name: review
description: 独立审查当前未提交改动，给出正确性/安全/可维护性意见
mode: isolated
bring_history: 0
model:
tools: [read, grep, glob, run]
---
# 代码变更审查

在独立对话中审查当前工作区的未提交改动（git diff），产出结构化审查意见。

1. 用 `git status --short` + `git diff` / `git diff --staged` 摸清变更范围；再用 read 通读关键被改文件的完整上下文（含相关调用方）。
2. 从四个维度逐项审查，每项给出【等级】：
   - 正确性：逻辑缺陷、边界、竞态、API 误用
   - 安全：越权、注入、路径逃逸、secrets 泄漏
   - 可维护性：结构、命名、重复、职责边界（对照 CLAUDE.md 的架构原则）
   - 测试覆盖：变更是否有对应单测，缺口在哪
3. 结论：列出【必须修复】【建议】两类，标注文件:行号；无改动则明确说明所见。

用户补充关注点：{input}
模板参考：见同目录 template.md 的审查格式。