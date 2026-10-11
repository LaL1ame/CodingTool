"""配置模型 + 状态机/触发/熔断单测(N3 / F3 / F6)。"""

import asyncio

import pytest

from codepilot.context.budget import ContextConfig, ContextBudget
from codepilot.context.estimator import TokenEstimator
from codepilot.config import load_context_config


def _budget(window=200_000, auto_margin=13_000, anchor=0):
    est = TokenEstimator(last_input_tokens=anchor)
    cfg = ContextConfig(window=window, auto_margin=auto_margin)
    return ContextBudget(cfg, est), est


# —— N3: config 读取 ——

def test_load_context_config_default(tmp_path):
    from codepilot.context.budget import default_context_config
    p = tmp_path / "nocontext.yaml"
    p.write_text("providers:\n  - name: a\n    model: m\n")
    ctx = load_context_config(p)
    dft = default_context_config()
    assert ctx.window == dft.window == 200_000
    assert ctx.auto_margin == 13_000


def test_load_context_config_override(tmp_path):
    p = tmp_path / "ctx.yaml"
    p.write_text("context:\n  window: 128000\n  auto_margin: 5000\n"
                 "  tool_threshold_n: 2000\n")
    ctx = load_context_config(p)
    assert ctx.window == 128000
    assert ctx.auto_margin == 5000
    assert ctx.tool_threshold_n == 2000
    assert ctx.manual_margin == 3000  # 未配置保持默认


def test_load_context_config_missing_file(tmp_path):
    ctx = load_context_config(tmp_path / "absent.yaml")
    assert ctx.window == 200_000


# —— F3: 触发判断 ——

def test_no_compact_when_room():
    b, est = _budget()
    est.anchor(1000)  # 远低于窗口
    est.add("a" * 100_000)  # 25k token,还有空间
    assert not asyncio.run(b.should_compact())


def test_compact_when_approaching_window():
    b, est = _budget(window=200_000, auto_margin=13_000)
    est.anchor(190_000)  # 逼近
    assert b.allows_auto_compact
    assert asyncio.run(b.should_compact())


def test_compact_at_trigger_boundary():
    # occupied == window - auto_margin 时不触发(需严格大于)
    b, est = _budget(window=200_000, auto_margin=13_000)
    est.anchor(200_000 - 13_000)
    assert not b.allows_auto_compact


# —— F6: 熔断 ——

def _budget_near(b):
    """把占用锚定到逼近窗口(触发前提),便于隔离熔断因素的差异。"""
    b.estimator.anchor(b.config.window - b.config.auto_margin + 1)


def test_circuit_opens_after_three_failures():
    b, _ = _budget()
    _budget_near(b)
    assert b.allows_auto_compact  # 未熔断且逼近 → 触发
    for _ in range(3):
        b.register_summarize_failure()
    assert b.is_openckety
    assert not b.allows_auto_compact  # 熔断后不再自动压缩


def test_circuit_no_open_under_three():
    b, _ = _budget()
    b.register_summarize_failure()
    b.register_summarize_failure()
    assert not b.is_openckety


def test_success_resets_failures():
    b, _ = _budget()
    b.register_summarize_failure()
    b.register_summarize_failure()
    b.summarize_succeeded()
    assert not b.is_openckety
    # 再失败一次不作为第3次
    b.register_summarize_failure()
    assert not b.is_openckety


def test_reset_circuit():
    b, _ = _budget()
    _budget_near(b)
    for _ in range(3):
        b.register_summarize_failure()
    assert b.is_openckety
    assert not b.allows_auto_compact
    b.reset_circuit()
    assert not b.is_openckety
    assert b.allows_auto_compact  # 解除熔断后恢复触发


# —— 层1 回收登记 ——

def test_record_offload_reduces_effective_occupied():
    b, est = _budget(anchor=100_000)
    before = b.effective_occupied
    b.record_offload(20_000)
    assert b.effective_occupied == before - 20_000


def test_anchor_resets_credit():
    b, est = _budget(anchor=100_000)
    b.record_offload(20_000)
    assert b.effective_occupied == 80_000
    b.anchor(105_000)  # 每轮重锚,抵免归零
    assert b.effective_occupied == 105_000