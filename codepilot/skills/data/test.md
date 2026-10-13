---
name: test
description: 定位并运行相关测试，报告结果，红了就修
mode: shared
tools: [run, read, grep, glob]
---
# 运行并修复测试

按 SOP 处理当前改动相关的测试。

1. 用 grep/glob 定位相关测试文件（本仓库：`tests/` 下，按被测模块匹配）。
2. 用 run 运行相关测试（如 `python -m pytest tests/test_xxx.py -q`）；不要默认全量跑，除非改动面很大。
3. 报告：绿色列出通过数/耗时；红色给出失败用例名 + 关键报错，并用 read 查看失败点相关源码，分析根因后小步修复，修复后重跑该测试直到绿。
4. 若属「需求导致既有测试语义变更」，先说明再改测试，不静默改断言掩盖问题。

用户附加要求：{input}。