#!/usr/bin/env bash
#
# CodePilot CI/CD 全自动化测试脚本
# 依据 docs/state4-agent-loop/test-spec.md 第 3 节（可自动化测试项）编写。
#
# 覆盖（全部自动化、exit code 非 0 即失败）：
#   1. 环境检查（Python 解释器与版本）
#   2. 依赖安装（--install 时执行 pip install -e .）
#   3. 静态检查：语法（py_compile）+ 导入 / 循环依赖
#   4. 全量单元测试（pytest，主门禁）
#   5.（--per-module）分模块测试明细，便于定位失败层
#
# 不含：TUI 渲染 / 真实 API 端到端（见 test-spec.md 第 6 节，需人工验收）。
#
# 用法：
#   ./ci/run-tests.sh              # 本地快速跑（不装依赖）
#   ./ci/run-tests.sh --install    # 先装依赖再跑（CI 环境）
#   ./ci/run-tests.sh --per-module # 额外输出分模块明细
#   ./ci/run-tests.sh --help       # 帮助

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# ---- 颜色（非 TTY 时关闭，避免 CI 日志出现转义码）----
if [ -t 1 ]; then
  GREEN=$'\033[0;32m'; RED=$'\033[0;31m'; YELLOW=$'\033[1;33m'; NC=$'\033[0m'
else
  GREEN=''; RED=''; YELLOW=''; NC=''
fi

step() { printf "\n%b==> %s%b\n" "$YELLOW" "$1" "$NC"; }
pass() { printf "%b[PASS]%b %s\n" "$GREEN" "$NC" "$1"; }
warn() { printf "%b[WARN]%b %s\n" "$YELLOW" "$NC" "$1"; }
die()  { printf "%b[FAIL]%b %s\n" "$RED" "$NC" "$1"; exit 1; }

usage() {
  sed -n '3,22p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
}

# ---- 参数解析 ----
INSTALL=0
PER_MODULE=0
for arg in "$@"; do
  case "$arg" in
    --install)    INSTALL=1 ;;
    --per-module) PER_MODULE=1 ;;
    --help|-h)    usage ;;
    *) die "未知参数: $arg（用 --help 查看用法）" ;;
  esac
done

# ---- Python 解释器解析 ----
PYTHON="${PYTHON:-python}"
command -v "$PYTHON" >/dev/null 2>&1 || PYTHON=python3
command -v "$PYTHON" >/dev/null 2>&1 || die "未找到 python，请通过 PYTHON 环境变量指定"

# ---- 1. 环境检查 ----
step "环境检查"
"$PYTHON" --version

# ---- 2. 依赖安装 ----
if [ "$INSTALL" = "1" ]; then
  step "安装依赖（pip install -e .）"
  "$PYTHON" -m pip install -e . -q
  pass "依赖安装完成"
fi

# ---- 3. 静态检查 ----
step "语法检查（py_compile）"
"$PYTHON" -m py_compile codepilot/*.py codepilot/tools/*.py tests/*.py
pass "语法检查通过"

step "导入 / 循环依赖检查（核心模块）"
"$PYTHON" -c "import codepilot, codepilot.agent, codepilot.chat, codepilot.config, codepilot.protocols, codepilot.tools"
pass "核心模块导入通过"

if "$PYTHON" -c "import textual" 2>/dev/null; then
  "$PYTHON" -c "import codepilot.app, codepilot.main"
  pass "全模块导入通过（含 TUI）"
else
  warn "textual 未安装，跳过 app/main 导入检查（本地可运行；CI 请加 --install）"
fi

# ---- 4. 全量单元测试（主门禁）----
step "全量单元测试"
"$PYTHON" -m pytest tests/ -q --tb=short
pass "pytest 全量通过"

# ---- 5. 分模块明细（可选，诊断用）----
if [ "$PER_MODULE" = "1" ]; then
  step "分模块测试明细"
  for f in tests/test_*.py; do
    out=$("$PYTHON" -m pytest "$f" -q --tb=no 2>&1 | tail -1 || true)
    printf "  %-30s %s\n" "$(basename "$f")" "$out"
  done
fi

# ---- 总结 ----
printf "\n%bALL CHECKS PASSED%b\n" "$GREEN" "$NC"
